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
    User,
    UserStorageUsage,
)

PURGE_TORRENT_JOB = "PURGE_TORRENT"
ACTIVE_REQUEST_STATES = (
    TorrentRequestState.REQUESTED,
    TorrentRequestState.ACTIVE,
    TorrentRequestState.READY,
)


@dataclass(frozen=True, slots=True)
class TorrentCancellationResult:
    request_id: uuid.UUID
    managed_torrent_id: uuid.UUID
    cancelled: bool
    purge_scheduled: bool
    purge_after: datetime | None


async def cancel_owned_torrent_request(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    torrent_request_id: uuid.UUID,
    retention_hours: int,
    now: datetime | None = None,
) -> TorrentCancellationResult | None:
    if not 1 <= retention_hours <= 2160:
        raise ValueError("torrent retention is invalid")
    timestamp = now or datetime.now(UTC)
    owner = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if owner is None:
        return None
    request = await session.scalar(
        select(TorrentRequest).where(
            TorrentRequest.id == torrent_request_id,
            TorrentRequest.user_id == user_id,
        )
    )
    if request is None:
        return None
    torrent = await session.get(ManagedTorrent, request.managed_torrent_id, with_for_update=True)
    request = await session.get(TorrentRequest, request.id, with_for_update=True)
    if torrent is None or request is None:
        return None
    if request.state is TorrentRequestState.CANCELLED:
        return TorrentCancellationResult(request.id, torrent.id, False, False, torrent.purge_after)
    if request.state not in ACTIVE_REQUEST_STATES:
        return None

    request.state = TorrentRequestState.CANCELLED
    request.cancelled_at = timestamp
    request.updated_at = timestamp
    usage = await session.get(UserStorageUsage, user_id, with_for_update=True)
    if usage is not None:
        usage.logical_bytes = max(0, usage.logical_bytes - torrent.total_size)
        usage.updated_at = timestamp

    remaining = await session.scalar(
        select(func.count())
        .select_from(TorrentRequest)
        .where(
            TorrentRequest.managed_torrent_id == torrent.id,
            TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
        )
    )
    if remaining is None or remaining > 0:
        await session.flush()
        return TorrentCancellationResult(request.id, torrent.id, True, False, None)

    purge_after = timestamp + timedelta(hours=retention_hours)
    torrent.lifecycle_generation += 1
    torrent.state = ManagedTorrentState.PURGE_PENDING
    torrent.purge_after = purge_after
    torrent.desired_active = False
    torrent.desired_priority = None
    torrent.updated_at = timestamp
    active_jobs = list(
        (
            await session.scalars(
                select(TorrentJob)
                .where(
                    TorrentJob.managed_torrent_id == torrent.id,
                    TorrentJob.state.in_((TorrentJobState.QUEUED, TorrentJobState.RUNNING)),
                )
                .with_for_update()
            )
        ).all()
    )
    for job in active_jobs:
        job.cancel_requested_at = timestamp
        job.updated_at = timestamp
        if job.state is TorrentJobState.QUEUED:
            job.state = TorrentJobState.CANCELLED
            job.finished_at = timestamp
    session.add(
        TorrentJob(
            managed_torrent_id=torrent.id,
            torrent_request_id=request.id,
            job_type=PURGE_TORRENT_JOB,
            idempotency_key=f"purge:{torrent.id}:{torrent.lifecycle_generation}",
            state=TorrentJobState.QUEUED,
            max_attempts=20,
            available_at=purge_after,
        )
    )
    await session.flush()
    return TorrentCancellationResult(request.id, torrent.id, True, True, purge_after)
