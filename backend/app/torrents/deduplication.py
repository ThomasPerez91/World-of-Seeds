from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    StoragePressureState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.models.base import utc_now
from app.storage.accounting import (
    StorageAdmissionPolicy,
    StorageDiskSnapshot,
    apply_storage_accounting,
    prepare_storage_accounting,
)

_INFO_HASH_RE = re.compile(r"^[0-9a-f]{40}$")
_ACTIVE_REQUEST_STATES = (
    TorrentRequestState.REQUESTED,
    TorrentRequestState.ACTIVE,
    TorrentRequestState.READY,
)
_MAX_BIGINT = 2**63 - 1


class TorrentDeduplicationError(ValueError):
    """Base error for a rejected V2 managed-torrent request."""


class TorrentRequestOwnerError(TorrentDeduplicationError):
    """Raised when the durable owner is missing or inactive."""


class TorrentMetadataConflictError(TorrentDeduplicationError):
    """Raised when one infohash is presented with inconsistent canonical metadata."""


class TorrentDeduplicationRaceError(RuntimeError):
    """Raised if a successful SQL upsert cannot be reconciled to its authoritative row."""


@dataclass(frozen=True, slots=True)
class ManagedTorrentRequestResult:
    managed_torrent: ManagedTorrent
    request: TorrentRequest
    managed_torrent_created: bool
    request_created: bool
    storage_pressure: StoragePressureState


async def create_or_get_torrent_request(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    info_hash: str,
    name: str,
    total_size: int,
    now: datetime | None = None,
    storage_policy: StorageAdmissionPolicy | None = None,
    disk_snapshot: StorageDiskSnapshot | None = None,
) -> ManagedTorrentRequestResult:
    """Converge one canonical torrent and one active right without committing the transaction."""

    _validate_metadata(info_hash=info_hash, name=name, total_size=total_size)
    timestamp = now or utc_now()
    if timestamp.utcoffset() is None:
        raise TorrentDeduplicationError("now must be timezone-aware")

    owner = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if owner is None or not owner.is_active or owner.deleted_at is not None:
        raise TorrentRequestOwnerError("the torrent request owner is missing or inactive")

    accounting = await prepare_storage_accounting(
        session,
        user_id=user_id,
        info_hash=info_hash,
        total_size=total_size,
        policy=storage_policy,
        disk=disk_snapshot,
        now=timestamp,
    )

    managed_id = uuid.uuid4()
    inserted_managed_id = await _insert_managed_torrent(
        session,
        managed_id=managed_id,
        storage_key=uuid.uuid4(),
        info_hash=info_hash,
        name=name,
        total_size=total_size,
        timestamp=timestamp,
    )
    managed_torrent = await session.scalar(
        select(ManagedTorrent).where(ManagedTorrent.info_hash == info_hash).with_for_update()
    )
    if managed_torrent is None:
        raise TorrentDeduplicationRaceError("managed torrent upsert did not produce a row")
    if managed_torrent.name != name or managed_torrent.total_size != total_size:
        raise TorrentMetadataConflictError(
            "the canonical torrent metadata conflicts with the existing infohash"
        )

    request_id = uuid.uuid4()
    inserted_request_id = await _insert_torrent_request(
        session,
        request_id=request_id,
        user_id=user_id,
        managed_torrent_id=managed_torrent.id,
        timestamp=timestamp,
    )
    request = await session.scalar(
        select(TorrentRequest)
        .where(
            TorrentRequest.user_id == user_id,
            TorrentRequest.managed_torrent_id == managed_torrent.id,
            TorrentRequest.state.in_(_ACTIVE_REQUEST_STATES),
        )
        .with_for_update()
    )
    if request is None:
        raise TorrentDeduplicationRaceError("torrent request upsert did not produce a row")

    apply_storage_accounting(
        accounting,
        request_created=inserted_request_id is not None,
        managed_torrent_created=inserted_managed_id is not None,
        total_size=total_size,
        now=timestamp,
    )

    return ManagedTorrentRequestResult(
        managed_torrent=managed_torrent,
        request=request,
        managed_torrent_created=inserted_managed_id is not None,
        request_created=inserted_request_id is not None,
        storage_pressure=accounting.pressure,
    )


def _validate_metadata(*, info_hash: str, name: str, total_size: int) -> None:
    if _INFO_HASH_RE.fullmatch(info_hash) is None:
        raise TorrentDeduplicationError("info_hash must be a canonical lowercase SHA-1 hash")
    if not 1 <= len(name) <= 4096 or "\x00" in name:
        raise TorrentDeduplicationError("torrent name must contain between 1 and 4096 characters")
    if type(total_size) is not int or not 0 <= total_size <= _MAX_BIGINT:
        raise TorrentDeduplicationError("total_size must be a non-negative signed 64-bit integer")


async def _insert_managed_torrent(
    session: AsyncSession,
    *,
    managed_id: uuid.UUID,
    storage_key: uuid.UUID,
    info_hash: str,
    name: str,
    total_size: int,
    timestamp: datetime,
) -> uuid.UUID | None:
    values = {
        "id": managed_id,
        "info_hash": info_hash,
        "storage_key": storage_key,
        "name": name,
        "total_size": total_size,
        "state": ManagedTorrentState.PENDING,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return await session.scalar(
            postgresql_insert(ManagedTorrent)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(ManagedTorrent.id)
        )
    if dialect == "sqlite":
        return await session.scalar(
            sqlite_insert(ManagedTorrent)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(ManagedTorrent.id)
        )
    raise RuntimeError(f"Unsupported database dialect for torrent deduplication: {dialect}")


async def _insert_torrent_request(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    user_id: uuid.UUID,
    managed_torrent_id: uuid.UUID,
    timestamp: datetime,
) -> uuid.UUID | None:
    values = {
        "id": request_id,
        "user_id": user_id,
        "managed_torrent_id": managed_torrent_id,
        "state": TorrentRequestState.REQUESTED,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return await session.scalar(
            postgresql_insert(TorrentRequest)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(TorrentRequest.id)
        )
    if dialect == "sqlite":
        return await session.scalar(
            sqlite_insert(TorrentRequest)
            .values(**values)
            .on_conflict_do_nothing()
            .returning(TorrentRequest.id)
        )
    raise RuntimeError(f"Unsupported database dialect for torrent deduplication: {dialect}")
