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
from app.scheduler.queue_visibility import is_ranked_queue_member

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
    queue_membership_changed: bool = False


@dataclass(frozen=True, slots=True)
class ExpiredTorrentRequest:
    user_id: uuid.UUID
    request_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class TorrentExpirationResult:
    managed_torrent_id: uuid.UUID
    requests: tuple[ExpiredTorrentRequest, ...]
    purge_job_id: uuid.UUID


def retention_days_for_popularity(distinct_users: int) -> int:
    if distinct_users < 1:
        raise ValueError("torrent popularity must be positive")
    if distinct_users >= 10:
        return 10
    return min(9, 5 + distinct_users // 2)


async def extend_ready_torrent_retention(
    session: AsyncSession,
    torrent: ManagedTorrent,
    *,
    now: datetime,
) -> datetime:
    """Set the first READY timestamp and only ever extend its popularity deadline."""

    if torrent.state is not ManagedTorrentState.READY:
        raise ValueError("torrent must be READY to calculate retention")
    if now.utcoffset() is None:
        raise ValueError("retention timestamp must be timezone-aware")
    ready_at = now if torrent.ready_at is None else _as_utc(torrent.ready_at)
    popularity = await session.scalar(
        select(func.count(func.distinct(TorrentRequest.user_id))).where(
            TorrentRequest.managed_torrent_id == torrent.id
        )
    )
    retention_days = retention_days_for_popularity(max(1, popularity or 0))
    candidate = ready_at + timedelta(days=retention_days)
    if torrent.ready_at is None:
        torrent.ready_at = ready_at
    if torrent.retention_expires_at is None or _as_utc(torrent.retention_expires_at) < candidate:
        torrent.retention_expires_at = candidate
    return _as_utc(torrent.retention_expires_at)


async def expire_ready_torrents_batch(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int = 200,
) -> tuple[TorrentExpirationResult, ...]:
    """Expire one indexed, locked batch; PostgreSQL remains the durable authority."""

    if now.utcoffset() is None:
        raise ValueError("expiration timestamp must be timezone-aware")
    if not 1 <= limit <= 500:
        raise ValueError("expiration batch limit must be between 1 and 500")
    due = tuple(
        (
            await session.scalars(
                select(ManagedTorrent)
                .where(
                    ManagedTorrent.state == ManagedTorrentState.READY,
                    ManagedTorrent.retention_expires_at.is_not(None),
                    ManagedTorrent.retention_expires_at <= _database_timestamp(now),
                )
                .order_by(ManagedTorrent.retention_expires_at, ManagedTorrent.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        ).all()
    )
    results: list[TorrentExpirationResult] = []
    for torrent in due:
        requests = tuple(
            (
                await session.scalars(
                    select(TorrentRequest)
                    .where(
                        TorrentRequest.managed_torrent_id == torrent.id,
                        TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
                    )
                    .order_by(TorrentRequest.user_id, TorrentRequest.id)
                    .with_for_update()
                )
            ).all()
        )
        user_ids = tuple(dict.fromkeys(request.user_id for request in requests))
        usages = {
            usage.user_id: usage
            for usage in (
                (
                    await session.scalars(
                        select(UserStorageUsage)
                        .where(UserStorageUsage.user_id.in_(user_ids))
                        .order_by(UserStorageUsage.user_id)
                        .with_for_update()
                    )
                ).all()
                if user_ids
                else ()
            )
        }
        for request in requests:
            request.state = TorrentRequestState.EXPIRED
            request.expires_at = now
            request.updated_at = now
            usage = usages.get(request.user_id)
            if usage is not None:
                usage.logical_bytes = max(0, usage.logical_bytes - torrent.total_size)
                usage.updated_at = now
        purge_job = await _enter_purge_pending(
            session,
            torrent,
            origin_request_id=requests[0].id if requests else None,
            purge_after=now,
            now=now,
        )
        results.append(
            TorrentExpirationResult(
                managed_torrent_id=torrent.id,
                requests=tuple(
                    ExpiredTorrentRequest(request.user_id, request.id) for request in requests
                ),
                purge_job_id=purge_job.id,
            )
        )
    await session.flush()
    return tuple(results)


async def cancel_owned_torrent_request(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    torrent_request_id: uuid.UUID,
    retention_hours: int,
    now: datetime | None = None,
    detect_queue_membership: bool = True,
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

    was_ranked = (
        await is_ranked_queue_member(session, torrent, now=timestamp)
        if detect_queue_membership
        else False
    )
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
        is_ranked = (
            await is_ranked_queue_member(session, torrent, now=timestamp)
            if detect_queue_membership
            else False
        )
        return TorrentCancellationResult(
            request.id,
            torrent.id,
            True,
            False,
            None,
            was_ranked != is_ranked,
        )

    purge_after = timestamp + timedelta(hours=retention_hours)
    await _enter_purge_pending(
        session,
        torrent,
        origin_request_id=request.id,
        purge_after=purge_after,
        now=timestamp,
    )
    await session.flush()
    is_ranked = (
        await is_ranked_queue_member(session, torrent, now=timestamp)
        if detect_queue_membership
        else False
    )
    return TorrentCancellationResult(
        request.id,
        torrent.id,
        True,
        True,
        purge_after,
        was_ranked != is_ranked,
    )


async def _enter_purge_pending(
    session: AsyncSession,
    torrent: ManagedTorrent,
    *,
    origin_request_id: uuid.UUID | None,
    purge_after: datetime,
    now: datetime,
) -> TorrentJob:
    torrent.lifecycle_generation += 1
    torrent.state = ManagedTorrentState.PURGE_PENDING
    torrent.purge_after = purge_after
    torrent.desired_active = False
    torrent.desired_priority = None
    torrent.desired_download_limit = 0
    torrent.purge_stop_pending = True
    torrent.updated_at = now
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
        job.cancel_requested_at = now
        job.updated_at = now
        if job.state is TorrentJobState.QUEUED:
            job.state = TorrentJobState.CANCELLED
            job.finished_at = now
    purge_job = TorrentJob(
        managed_torrent_id=torrent.id,
        torrent_request_id=origin_request_id,
        job_type=PURGE_TORRENT_JOB,
        idempotency_key=f"purge:{torrent.id}:{torrent.lifecycle_generation}",
        state=TorrentJobState.QUEUED,
        max_attempts=20,
        available_at=purge_after,
    )
    session.add(purge_job)
    await session.flush()
    return purge_job


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _database_timestamp(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)
