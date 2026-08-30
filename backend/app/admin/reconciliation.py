from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.qbittorrent_v2 import QBittorrentV2Inventory
from app.models import (
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    StorageLedger,
    TorrentFile,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    UserStorageUsage,
)
from app.storage.shared import SharedContentInventory

type ReconciliationSeverity = Literal["info", "warning", "critical"]
type RecoveryAction = Literal["cancel_requests", "purge_metadata"]
ACTIVE_REQUEST_STATES = (
    TorrentRequestState.REQUESTED,
    TorrentRequestState.ACTIVE,
    TorrentRequestState.READY,
)
MAX_RECOVERY_REQUESTS = 1000
MAX_RECONCILIATION_CURSOR_BYTES = 4096
RECOVER_CANCEL_REQUESTS_JOB = "RECOVER_CANCEL_REQUESTS"
RECOVER_PURGE_METADATA_JOB = "RECOVER_PURGE_METADATA"
RECOVERY_JOB_TYPES = (RECOVER_CANCEL_REQUESTS_JOB, RECOVER_PURGE_METADATA_JOB)


class ReconciliationRecoveryError(RuntimeError):
    """A bounded, secret-safe recovery refusal."""


class ReconciliationCursorError(ValueError):
    """An opaque reconciliation cursor is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class ReconciliationCursor:
    phase: Literal["database", "qbittorrent", "storage"]
    after: str | None = None
    snapshot_ids: tuple[uuid.UUID, ...] = ()
    snapshot_index: int = 0

    def encode(self) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "phase": self.phase,
                "after": self.after,
                "snapshot_ids": [str(value) for value in self.snapshot_ids],
                "snapshot_index": self.snapshot_index,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def decode(cls, value: str) -> ReconciliationCursor:
        if not 1 <= len(value) <= MAX_RECONCILIATION_CURSOR_BYTES:
            raise ReconciliationCursorError("reconciliation_cursor_invalid")
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
            if not isinstance(payload, dict) or set(payload) != {
                "v",
                "phase",
                "after",
                "snapshot_ids",
                "snapshot_index",
            }:
                raise ValueError
            if payload["v"] != 1 or payload["phase"] not in {
                "database",
                "qbittorrent",
                "storage",
            }:
                raise ValueError
            after = payload["after"]
            snapshot_index = payload["snapshot_index"]
            raw_snapshot_ids = payload["snapshot_ids"]
            if (
                (after is not None and (not isinstance(after, str) or len(after) > 128))
                or not isinstance(snapshot_index, int)
                or snapshot_index < 0
            ):
                raise ValueError
            if not isinstance(raw_snapshot_ids, list) or len(raw_snapshot_ids) > 16:
                raise ValueError
            snapshot_ids = tuple(uuid.UUID(item) for item in raw_snapshot_ids)
            if len(snapshot_ids) != len(set(snapshot_ids)) or snapshot_index > len(snapshot_ids):
                raise ValueError
            return cls(payload["phase"], after, snapshot_ids, snapshot_index)
        except (binascii.Error, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ReconciliationCursorError("reconciliation_cursor_invalid") from exc


@dataclass(frozen=True, slots=True)
class ReconciliationRecoveryResult:
    managed_torrent_id: uuid.UUID
    state: ManagedTorrentState
    cancelled_requests: int
    metadata_purged: bool
    qbittorrent_present: bool
    storage_present: bool


@dataclass(frozen=True, slots=True)
class ReconciliationRecoverySnapshot:
    managed_torrent_id: uuid.UUID
    info_hash: str
    storage_key: uuid.UUID
    qbittorrent_account_ref: uuid.UUID | None
    lifecycle_generation: int
    active_request_ids: tuple[uuid.UUID, ...]
    active_job_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationAnomaly:
    code: str
    severity: ReconciliationSeverity
    resource_id: str | None
    action: str


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    database_scanned: int
    qbittorrent_scanned: int
    storage_scanned: int
    external_torrents: int
    anomalies: tuple[ReconciliationAnomaly, ...]
    truncated: bool


def reconcile_inventory(
    torrents: tuple[ManagedTorrent, ...],
    *,
    database_truncated: bool,
    qbittorrent: QBittorrentV2Inventory | None,
    storage: SharedContentInventory | None,
) -> ReconciliationReport:
    """Compare bounded read-only inventories without mutating qB, SQL, or storage."""
    anomalies: list[ReconciliationAnomaly] = []
    by_hash = {torrent.info_hash: torrent for torrent in torrents}
    by_storage = {torrent.storage_key: torrent for torrent in torrents}
    qb_items = qbittorrent.items if qbittorrent is not None else ()
    qb_by_hash = {item.info_hash: item for item in qb_items}
    storage_keys = set(storage.keys if storage is not None else ())

    if qbittorrent is None:
        anomalies.append(
            ReconciliationAnomaly("qbittorrent_unavailable", "warning", None, "retry_inventory")
        )
    if storage is None:
        anomalies.append(
            ReconciliationAnomaly("storage_unavailable", "critical", None, "inspect_storage")
        )

    for torrent in torrents:
        resource_id = str(torrent.id)
        expects_physical = torrent.state not in {
            ManagedTorrentState.PENDING,
            ManagedTorrentState.PURGED,
        }
        qb_missing = (
            expects_physical
            and qbittorrent is not None
            and not qbittorrent.truncated
            and torrent.info_hash not in qb_by_hash
        )
        storage_missing = (
            expects_physical and storage is not None and torrent.storage_key not in storage_keys
        )
        if qb_missing:
            anomalies.append(
                ReconciliationAnomaly(
                    "missing_qb_torrent",
                    "critical",
                    resource_id,
                    "purge_metadata" if storage_missing else "cancel_requests",
                )
            )
        qb_item = qb_by_hash.get(torrent.info_hash)
        if qb_item is not None and qb_item.storage_key != torrent.storage_key:
            anomalies.append(
                ReconciliationAnomaly(
                    "qb_identity_mismatch", "critical", resource_id, "manual_review"
                )
            )
        if storage_missing:
            anomalies.append(
                ReconciliationAnomaly(
                    "missing_storage",
                    "critical",
                    resource_id,
                    "purge_metadata" if qb_missing else "cancel_requests",
                )
            )

    external = 0
    for item in qb_items:
        if not item.claims_wos_identity:
            external += 1
            continue
        matched_torrent = by_hash.get(item.info_hash)
        if matched_torrent is None and not database_truncated:
            anomalies.append(
                ReconciliationAnomaly("orphan_wos_qb", "warning", None, "manual_review")
            )
    if external:
        anomalies.append(ReconciliationAnomaly("external_torrents_read_only", "info", None, "none"))

    if storage is not None:
        if storage.invalid_entries:
            anomalies.append(
                ReconciliationAnomaly("unsafe_storage_entries", "critical", None, "manual_review")
            )
        if not database_truncated:
            for key in storage_keys.difference(by_storage):
                anomalies.append(
                    ReconciliationAnomaly("orphan_storage", "warning", str(key), "manual_review")
                )

    return ReconciliationReport(
        database_scanned=len(torrents),
        qbittorrent_scanned=len(qb_items),
        storage_scanned=len(storage_keys),
        external_torrents=external,
        anomalies=tuple(anomalies),
        truncated=(
            database_truncated
            or (qbittorrent.truncated if qbittorrent is not None else False)
            or (storage.truncated if storage is not None else False)
        ),
    )


async def recover_orphaned_torrent(
    session: AsyncSession,
    managed_torrent_id: uuid.UUID,
    *,
    action: RecoveryAction,
    qbittorrent_present: bool,
    storage_present: bool,
    expected: ReconciliationRecoverySnapshot,
    now: datetime | None = None,
) -> ReconciliationRecoveryResult:
    """Recover SQL ownership only; this function never mutates qB or filesystem data."""
    timestamp = now or datetime.now(UTC)
    torrent = await session.get(ManagedTorrent, managed_torrent_id, with_for_update=True)
    if torrent is None:
        raise ReconciliationRecoveryError("managed_torrent_not_found")
    current_request_ids = tuple(
        (
            await session.scalars(
                select(TorrentRequest.id)
                .where(
                    TorrentRequest.managed_torrent_id == torrent.id,
                    TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
                )
                .order_by(TorrentRequest.id)
            )
        ).all()
    )
    current_job_ids = tuple(
        (
            await session.scalars(
                select(TorrentJob.id)
                .where(
                    TorrentJob.managed_torrent_id == torrent.id,
                    TorrentJob.state.in_((TorrentJobState.QUEUED, TorrentJobState.RUNNING)),
                    TorrentJob.job_type.not_in(RECOVERY_JOB_TYPES),
                )
                .order_by(TorrentJob.id)
            )
        ).all()
    )
    if (
        expected.managed_torrent_id != torrent.id
        or expected.lifecycle_generation != torrent.lifecycle_generation
        or expected.active_request_ids != current_request_ids
        or expected.active_job_ids != current_job_ids
    ):
        raise ReconciliationRecoveryError("recovery_state_changed")
    if torrent.state is ManagedTorrentState.PURGED:
        return ReconciliationRecoveryResult(
            torrent.id,
            torrent.state,
            0,
            True,
            qbittorrent_present,
            storage_present,
        )
    if qbittorrent_present and storage_present:
        raise ReconciliationRecoveryError("managed_torrent_not_orphaned")
    if action == "purge_metadata" and (qbittorrent_present or storage_present):
        raise ReconciliationRecoveryError("physical_state_requires_manual_review")
    if action == "purge_metadata" and current_job_ids:
        raise ReconciliationRecoveryError("recovery_jobs_active")

    requests = list(
        (
            await session.scalars(
                select(TorrentRequest)
                .where(
                    TorrentRequest.managed_torrent_id == torrent.id,
                    TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
                )
                .order_by(TorrentRequest.id)
                .with_for_update()
                .limit(MAX_RECOVERY_REQUESTS + 1)
            )
        ).all()
    )
    if len(requests) > MAX_RECOVERY_REQUESTS:
        raise ReconciliationRecoveryError("recovery_request_limit_exceeded")
    usages = {
        usage.user_id: usage
        for usage in (
            await session.scalars(
                select(UserStorageUsage)
                .where(UserStorageUsage.user_id.in_({item.user_id for item in requests}))
                .with_for_update()
            )
        ).all()
    }
    for request in requests:
        request.state = TorrentRequestState.CANCELLED
        request.cancelled_at = timestamp
        request.updated_at = timestamp
        usage = usages.get(request.user_id)
        if usage is not None:
            usage.logical_bytes = max(0, usage.logical_bytes - torrent.total_size)
            usage.updated_at = timestamp

    jobs = list(
        (
            await session.scalars(
                select(TorrentJob)
                .where(
                    TorrentJob.managed_torrent_id == torrent.id,
                    TorrentJob.state.in_((TorrentJobState.QUEUED, TorrentJobState.RUNNING)),
                    TorrentJob.job_type.not_in(RECOVERY_JOB_TYPES),
                )
                .with_for_update()
            )
        ).all()
    )
    for job in jobs:
        job.cancel_requested_at = timestamp
        job.updated_at = timestamp
        if job.state is TorrentJobState.QUEUED:
            job.state = TorrentJobState.CANCELLED
            job.finished_at = timestamp

    torrent.lifecycle_generation += 1
    torrent.desired_active = False
    torrent.desired_priority = None
    torrent.retry_at = None
    torrent.scheduler_retry_at = None
    torrent.purge_after = None
    torrent.updated_at = timestamp
    metadata_purged = action == "purge_metadata"
    if metadata_purged:
        await session.execute(
            delete(DownloadLease).where(DownloadLease.managed_torrent_id == torrent.id)
        )
        await session.execute(
            delete(TorrentFile).where(TorrentFile.managed_torrent_id == torrent.id)
        )
        torrent.state = ManagedTorrentState.PURGED
        torrent.qb_state = None
        torrent.progress = 0
        torrent.manifest_version = 0
        torrent.manifest_checksum = None
        torrent.manifest_file_count = 0
        torrent.manifest_total_size = 0
        ledger = await session.get(StorageLedger, 1, with_for_update=True)
        if ledger is not None:
            ledger.managed_bytes = max(0, ledger.managed_bytes - torrent.total_size)
            ledger.updated_at = timestamp
    else:
        torrent.state = ManagedTorrentState.ERROR

    await session.flush()
    return ReconciliationRecoveryResult(
        torrent.id,
        torrent.state,
        len(requests),
        metadata_purged,
        qbittorrent_present,
        storage_present,
    )


async def recovery_snapshot(
    session: AsyncSession,
    managed_torrent_id: uuid.UUID,
) -> ReconciliationRecoverySnapshot:
    torrent = await session.get(ManagedTorrent, managed_torrent_id)
    if torrent is None:
        raise ReconciliationRecoveryError("managed_torrent_not_found")
    request_ids = tuple(
        (
            await session.scalars(
                select(TorrentRequest.id)
                .where(
                    TorrentRequest.managed_torrent_id == torrent.id,
                    TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
                )
                .order_by(TorrentRequest.id)
                .limit(MAX_RECOVERY_REQUESTS + 1)
            )
        ).all()
    )
    job_ids = tuple(
        (
            await session.scalars(
                select(TorrentJob.id)
                .where(
                    TorrentJob.managed_torrent_id == torrent.id,
                    TorrentJob.state.in_((TorrentJobState.QUEUED, TorrentJobState.RUNNING)),
                    TorrentJob.job_type.not_in(RECOVERY_JOB_TYPES),
                )
                .order_by(TorrentJob.id)
                .limit(MAX_RECOVERY_REQUESTS + 1)
            )
        ).all()
    )
    if len(request_ids) > MAX_RECOVERY_REQUESTS or len(job_ids) > MAX_RECOVERY_REQUESTS:
        raise ReconciliationRecoveryError("recovery_resource_limit_exceeded")
    return ReconciliationRecoverySnapshot(
        torrent.id,
        torrent.info_hash,
        torrent.storage_key,
        torrent.qbittorrent_account_ref,
        torrent.lifecycle_generation,
        request_ids,
        job_ids,
    )
