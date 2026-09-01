from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    StoragePressureState,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.models.base import utc_now
from app.scheduler.queue_visibility import is_ranked_queue_member
from app.storage.accounting import (
    StorageAdmissionPolicy,
    StorageDiskSnapshot,
    apply_storage_accounting,
    prepare_storage_accounting,
)
from app.torrents.lifecycle import extend_ready_torrent_retention

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


class TorrentPurgeInProgressError(TorrentDeduplicationError):
    """A physical purge already owns the managed torrent lifecycle."""


@dataclass(frozen=True, slots=True)
class ManagedTorrentRequestResult:
    managed_torrent: ManagedTorrent
    request: TorrentRequest
    managed_torrent_created: bool
    request_created: bool
    managed_torrent_reactivated: bool
    retention_extended: bool
    storage_pressure: StoragePressureState
    queue_membership_changed: bool = False


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
    if managed_torrent.state is ManagedTorrentState.PURGING:
        raise TorrentPurgeInProgressError("managed torrent purge is in progress")
    was_ranked = await is_ranked_queue_member(session, managed_torrent, now=timestamp)
    if (
        managed_torrent.state
        in {
            ManagedTorrentState.READY,
            ManagedTorrentState.PURGE_PENDING,
        }
        and managed_torrent.retention_expires_at is not None
        and _as_utc(timestamp) >= _as_utc(managed_torrent.retention_expires_at)
    ):
        raise TorrentPurgeInProgressError("managed torrent retention has expired")
    reactivated = managed_torrent.state is ManagedTorrentState.PURGED
    if reactivated:
        managed_torrent.state = ManagedTorrentState.PENDING
        managed_torrent.progress = 0
        managed_torrent.qb_state = None
        managed_torrent.retry_at = None
        managed_torrent.purge_after = None
        managed_torrent.ready_at = None
        managed_torrent.retention_expires_at = None
        managed_torrent.purge_stop_pending = False
        managed_torrent.updated_at = timestamp
    elif managed_torrent.state is ManagedTorrentState.PURGE_PENDING:
        if managed_torrent.purge_after is None or _as_utc(timestamp) >= _as_utc(
            managed_torrent.purge_after
        ):
            raise TorrentPurgeInProgressError("managed torrent purge deadline has passed")
        managed_torrent.state = (
            ManagedTorrentState.READY
            if managed_torrent.progress >= 1
            else ManagedTorrentState.DOWNLOADING
        )
        managed_torrent.purge_after = None
        managed_torrent.purge_stop_pending = False
        managed_torrent.updated_at = timestamp
        purge_jobs = list(
            (
                await session.scalars(
                    select(TorrentJob)
                    .where(
                        TorrentJob.managed_torrent_id == managed_torrent.id,
                        TorrentJob.job_type == "PURGE_TORRENT",
                        TorrentJob.state.in_((TorrentJobState.QUEUED, TorrentJobState.RUNNING)),
                    )
                    .with_for_update()
                )
            ).all()
        )
        for job in purge_jobs:
            job.cancel_requested_at = timestamp
            job.updated_at = timestamp
            if job.state is TorrentJobState.QUEUED:
                job.state = TorrentJobState.CANCELLED
                job.finished_at = timestamp

    accounting = await prepare_storage_accounting(
        session,
        user_id=user_id,
        info_hash=info_hash,
        total_size=total_size,
        policy=storage_policy,
        disk=disk_snapshot,
        now=timestamp,
        physical_content_missing=(inserted_managed_id is not None or reactivated),
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
    if inserted_request_id is not None:
        if managed_torrent.state is ManagedTorrentState.READY:
            request.state = TorrentRequestState.READY
            request.ready_at = timestamp
        elif managed_torrent.state not in {
            ManagedTorrentState.PENDING,
            ManagedTorrentState.ADDING,
        }:
            request.state = TorrentRequestState.ACTIVE
        await session.flush()

    previous_retention_expires_at = (
        None
        if managed_torrent.retention_expires_at is None
        else _as_utc(managed_torrent.retention_expires_at)
    )
    if managed_torrent.state is ManagedTorrentState.READY:
        await extend_ready_torrent_retention(session, managed_torrent, now=timestamp)
    retention_extended = managed_torrent.retention_expires_at is not None and (
        previous_retention_expires_at is None
        or _as_utc(managed_torrent.retention_expires_at) > previous_retention_expires_at
    )

    apply_storage_accounting(
        accounting,
        request_created=inserted_request_id is not None,
        managed_torrent_created=(inserted_managed_id is not None or reactivated),
        total_size=total_size,
        now=timestamp,
    )
    await session.flush()
    queue_membership_changed = was_ranked != await is_ranked_queue_member(
        session,
        managed_torrent,
        now=timestamp,
    )

    return ManagedTorrentRequestResult(
        managed_torrent=managed_torrent,
        request=request,
        managed_torrent_created=inserted_managed_id is not None,
        request_created=inserted_request_id is not None,
        managed_torrent_reactivated=reactivated,
        retention_extended=retention_extended,
        storage_pressure=accounting.pressure,
        queue_membership_changed=queue_membership_changed,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
