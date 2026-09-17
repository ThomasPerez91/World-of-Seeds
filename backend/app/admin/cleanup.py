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
    UserStorageUsage,
)
from app.torrents import PURGE_TORRENT_JOB

ACTIVE_REQUEST_STATES = (
    TorrentRequestState.REQUESTED,
    TorrentRequestState.ACTIVE,
    TorrentRequestState.READY,
)
DOWNLOADED_TORRENT_STATES = (
    ManagedTorrentState.READY,
    ManagedTorrentState.PURGE_PENDING,
    ManagedTorrentState.PURGING,
)


@dataclass(frozen=True, slots=True)
class AdminCleanupItem:
    id: uuid.UUID
    name: str
    size_bytes: int
    subscriber_count: int
    deletion_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdminCleanupPurgeResult:
    requested: int
    scheduled_ids: tuple[uuid.UUID, ...]
    skipped_ids: tuple[uuid.UUID, ...]


async def list_downloaded_content(
    session: AsyncSession,
    *,
    retention_hours: int,
) -> tuple[AdminCleanupItem, ...]:
    if not 1 <= retention_hours <= 2160:
        raise ValueError("torrent retention is invalid")

    subscriber_count = (
        select(func.count(TorrentRequest.id))
        .where(
            TorrentRequest.managed_torrent_id == ManagedTorrent.id,
            TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
        )
        .correlate(ManagedTorrent)
        .scalar_subquery()
    )
    dated_subscriber_count = (
        select(func.count(TorrentRequest.unsubscribe_at))
        .where(
            TorrentRequest.managed_torrent_id == ManagedTorrent.id,
            TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
        )
        .correlate(ManagedTorrent)
        .scalar_subquery()
    )
    latest_unsubscribe_at = (
        select(func.max(TorrentRequest.unsubscribe_at))
        .where(
            TorrentRequest.managed_torrent_id == ManagedTorrent.id,
            TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
        )
        .correlate(ManagedTorrent)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(
                ManagedTorrent.id,
                ManagedTorrent.name,
                ManagedTorrent.total_size,
                ManagedTorrent.purge_after,
                subscriber_count.label("subscriber_count"),
                dated_subscriber_count.label("dated_subscriber_count"),
                latest_unsubscribe_at.label("latest_unsubscribe_at"),
            )
            .where(ManagedTorrent.state.in_(DOWNLOADED_TORRENT_STATES))
            .order_by(ManagedTorrent.name, ManagedTorrent.id)
        )
    ).all()

    items: list[AdminCleanupItem] = []
    for row in rows:
        subscribers = int(row.subscriber_count or 0)
        dated_subscribers = int(row.dated_subscriber_count or 0)
        deletion_at = row.purge_after
        if (
            deletion_at is None
            and subscribers > 0
            and dated_subscribers == subscribers
            and row.latest_unsubscribe_at is not None
        ):
            deletion_at = row.latest_unsubscribe_at + timedelta(hours=retention_hours)
        items.append(
            AdminCleanupItem(
                id=row.id,
                name=row.name,
                size_bytes=row.total_size,
                subscriber_count=subscribers,
                deletion_at=_as_utc(deletion_at) if deletion_at is not None else None,
            )
        )
    return tuple(items)


async def schedule_admin_purge(
    session: AsyncSession,
    torrent_ids: tuple[uuid.UUID, ...],
    *,
    now: datetime | None = None,
) -> AdminCleanupPurgeResult:
    timestamp = now or datetime.now(UTC)
    if timestamp.utcoffset() is None:
        raise ValueError("purge timestamp must be timezone-aware")
    unique_ids = tuple(dict.fromkeys(torrent_ids))
    torrents = {
        torrent.id: torrent
        for torrent in (
            (
                await session.scalars(
                    select(ManagedTorrent)
                    .where(ManagedTorrent.id.in_(unique_ids))
                    .order_by(ManagedTorrent.id)
                    .with_for_update()
                )
            ).all()
            if unique_ids
            else ()
        )
    }
    scheduled: list[uuid.UUID] = []
    skipped: list[uuid.UUID] = []
    for torrent_id in unique_ids:
        torrent = torrents.get(torrent_id)
        if torrent is None or torrent.state not in DOWNLOADED_TORRENT_STATES:
            skipped.append(torrent_id)
            continue

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
            request.state = TorrentRequestState.CANCELLED
            request.cancelled_at = timestamp
            request.updated_at = timestamp
            usage = usages.get(request.user_id)
            if usage is not None:
                usage.logical_bytes = max(0, usage.logical_bytes - torrent.total_size)
                usage.updated_at = timestamp

        active_purge_job = await session.scalar(
            select(TorrentJob)
            .where(
                TorrentJob.managed_torrent_id == torrent.id,
                TorrentJob.job_type == PURGE_TORRENT_JOB,
                TorrentJob.state.in_((TorrentJobState.QUEUED, TorrentJobState.RUNNING)),
            )
            .order_by(TorrentJob.created_at, TorrentJob.id)
            .with_for_update()
        )
        torrent.lifecycle_generation += 1
        torrent.purge_after = timestamp
        torrent.updated_at = timestamp
        if active_purge_job is None:
            session.add(
                TorrentJob(
                    managed_torrent_id=torrent.id,
                    torrent_request_id=None,
                    job_type=PURGE_TORRENT_JOB,
                    idempotency_key=(f"admin-purge:{torrent.id}:{torrent.lifecycle_generation}"),
                    state=TorrentJobState.QUEUED,
                    max_attempts=20,
                    available_at=timestamp,
                )
            )
        else:
            if active_purge_job.state is TorrentJobState.QUEUED:
                active_purge_job.available_at = timestamp
            active_purge_job.cancel_requested_at = None
            active_purge_job.updated_at = timestamp
        scheduled.append(torrent.id)

    await session.flush()
    return AdminCleanupPurgeResult(
        requested=len(unique_ids),
        scheduled_ids=tuple(scheduled),
        skipped_ids=tuple(skipped),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
