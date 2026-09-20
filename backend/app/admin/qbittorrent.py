from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
)
from app.torrents import PURGE_TORRENT_JOB

ACTIVE_REQUEST_STATES = (
    TorrentRequestState.REQUESTED,
    TorrentRequestState.ACTIVE,
    TorrentRequestState.READY,
)
RESUMABLE_STATES = (
    ManagedTorrentState.DOWNLOADING,
    ManagedTorrentState.PAUSED,
)
ADMIN_PURGE_STATES = tuple(
    state
    for state in ManagedTorrentState
    if state not in (ManagedTorrentState.PURGING, ManagedTorrentState.PURGED)
)


class AdminQBittorrentActionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AdminQBittorrentResumeResult:
    torrent_id: uuid.UUID
    info_hash: str
    storage_key: uuid.UUID
    qbittorrent_account_ref: uuid.UUID
    download_limit_bytes_per_second: int


async def force_resume_admin_torrent(
    session: AsyncSession,
    *,
    torrent_id: uuid.UUID,
    retention_hours: int,
    now: datetime | None = None,
) -> AdminQBittorrentResumeResult:
    if not 1 <= retention_hours <= 2160:
        raise ValueError("torrent retention is invalid")
    timestamp = now or datetime.now(UTC)
    if timestamp.utcoffset() is None:
        raise ValueError("resume timestamp must be timezone-aware")
    torrent = await session.get(ManagedTorrent, torrent_id, with_for_update=True)
    if torrent is None:
        raise AdminQBittorrentActionError("qbittorrent_torrent_not_found")
    if torrent.state not in RESUMABLE_STATES or torrent.qbittorrent_account_ref is None:
        raise AdminQBittorrentActionError("qbittorrent_resume_not_allowed")

    active_requests = int(
        await session.scalar(
            select(func.count())
            .select_from(TorrentRequest)
            .where(
                TorrentRequest.managed_torrent_id == torrent.id,
                TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
            )
        )
        or 0
    )
    if active_requests == 0:
        deadline = timestamp + timedelta(hours=retention_hours)
        if torrent.purge_after is None or _as_utc(torrent.purge_after) < deadline:
            torrent.purge_after = deadline
        purge_job = await session.scalar(
            select(TorrentJob)
            .where(
                TorrentJob.managed_torrent_id == torrent.id,
                TorrentJob.job_type == PURGE_TORRENT_JOB,
                TorrentJob.state.in_((TorrentJobState.QUEUED, TorrentJobState.RUNNING)),
            )
            .order_by(TorrentJob.created_at, TorrentJob.id)
            .with_for_update()
        )
        if purge_job is None:
            torrent.lifecycle_generation += 1
            session.add(
                TorrentJob(
                    managed_torrent_id=torrent.id,
                    torrent_request_id=None,
                    job_type=PURGE_TORRENT_JOB,
                    idempotency_key=f"admin-resume-purge:{torrent.id}:{torrent.lifecycle_generation}",
                    state=TorrentJobState.QUEUED,
                    max_attempts=20,
                    available_at=torrent.purge_after,
                )
            )
        elif purge_job.state is TorrentJobState.QUEUED:
            purge_job.available_at = torrent.purge_after
            purge_job.cancel_requested_at = None
            purge_job.updated_at = timestamp

    torrent.admin_forced_active = True
    torrent.purge_stop_pending = False
    torrent.updated_at = timestamp
    await session.flush()
    return AdminQBittorrentResumeResult(
        torrent.id,
        torrent.info_hash,
        torrent.storage_key,
        torrent.qbittorrent_account_ref,
        torrent.desired_download_limit,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
