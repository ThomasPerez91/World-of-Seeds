from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
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
from app.torrents import (
    backfill_ready_unsubscribe_deadlines_batch,
    cancel_owned_torrent_request,
    create_or_get_torrent_request,
    expire_ready_torrents_batch,
    mark_ready_requests,
)

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


async def _user(session: AsyncSession, name: str) -> User:
    user = User(username=name, password_hash="test-password-hash")
    session.add(user)
    await session.flush()
    return user


async def _shared_ready(
    session: AsyncSession,
    *,
    users: int = 1,
    auto_hours: int = 48,
    info_hash: str = "1" * 40,
) -> tuple[ManagedTorrent, tuple[TorrentRequest, ...], tuple[User, ...]]:
    owners = tuple([await _user(session, f"ready-{uuid.uuid4().hex[:8]}") for _ in range(users)])
    results = [
        await create_or_get_torrent_request(
            session,
            user_id=owner.id,
            info_hash=info_hash,
            name="Shared ready content",
            total_size=100,
            now=NOW,
            auto_unsubscribe_hours=auto_hours,
        )
        for owner in owners
    ]
    torrent = results[0].managed_torrent
    torrent.state = ManagedTorrentState.READY
    torrent.progress = 1
    requests = await mark_ready_requests(
        session,
        torrent,
        now=NOW,
        auto_unsubscribe_hours=auto_hours,
    )
    await session.flush()
    return torrent, requests, owners


@pytest.mark.asyncio
async def test_first_ready_assigns_independent_subscription_deadlines(
    db_session: AsyncSession,
) -> None:
    torrent, requests, _ = await _shared_ready(db_session, users=2, auto_hours=72)

    assert torrent.ready_at == NOW
    assert torrent.retention_expires_at is None
    assert len(requests) == 2
    assert all(request.state is TorrentRequestState.READY for request in requests)
    assert all(request.ready_at == NOW for request in requests)
    assert all(request.unsubscribe_at == NOW + timedelta(hours=72) for request in requests)


@pytest.mark.asyncio
async def test_joining_ready_content_gets_fresh_deadline_without_duplicate(
    db_session: AsyncSession,
) -> None:
    torrent, first_requests, _ = await _shared_ready(db_session, auto_hours=24)
    second = await _user(db_session, "late-ready-owner")
    joined_at = NOW + timedelta(hours=8)
    joined = await create_or_get_torrent_request(
        db_session,
        user_id=second.id,
        info_hash=torrent.info_hash,
        name=torrent.name,
        total_size=torrent.total_size,
        now=joined_at,
        auto_unsubscribe_hours=24,
    )
    repeated = await create_or_get_torrent_request(
        db_session,
        user_id=second.id,
        info_hash=torrent.info_hash,
        name=torrent.name,
        total_size=torrent.total_size,
        now=joined_at + timedelta(hours=1),
        auto_unsubscribe_hours=24,
    )

    assert joined.managed_torrent.id == torrent.id
    assert joined.request.ready_at == joined_at
    assert joined.request.unsubscribe_at == joined_at + timedelta(hours=24)
    assert repeated.request_created is False
    assert repeated.request.id == joined.request.id
    assert repeated.request.unsubscribe_at == joined.request.unsubscribe_at
    assert first_requests[0].unsubscribe_at == NOW + timedelta(hours=24)


@pytest.mark.asyncio
async def test_unsubscribe_one_owner_keeps_shared_content_ready(db_session: AsyncSession) -> None:
    torrent, requests, owners = await _shared_ready(db_session, users=2)
    torrent.qb_state = "uploading"
    result = await cancel_owned_torrent_request(
        db_session,
        user_id=requests[0].user_id,
        torrent_request_id=requests[0].id,
        retention_hours=12,
        now=NOW + timedelta(hours=1),
    )

    assert result is not None and result.cancelled and not result.purge_scheduled
    assert torrent.state is ManagedTorrentState.READY
    assert torrent.qb_state == "uploading"
    assert torrent.desired_active is False
    assert torrent.desired_priority is None
    assert torrent.desired_download_limit == 0
    assert torrent.purge_stop_pending is False
    assert torrent.purge_after is None
    assert requests[0].state is TorrentRequestState.CANCELLED
    assert requests[1].state is TorrentRequestState.READY
    usage = await db_session.get(UserStorageUsage, requests[0].user_id)
    assert usage is not None and usage.logical_bytes == 0
    assert await db_session.scalar(select(func.count()).select_from(TorrentJob)) == 0


@pytest.mark.asyncio
async def test_last_unsubscribe_schedules_physical_grace_once(db_session: AsyncSession) -> None:
    torrent, requests, owners = await _shared_ready(db_session)
    torrent.qb_state = "stalledup"
    cancelled_at = NOW + timedelta(hours=2)
    first = await cancel_owned_torrent_request(
        db_session,
        user_id=owners[0].id,
        torrent_request_id=requests[0].id,
        retention_hours=36,
        now=cancelled_at,
    )
    replay = await cancel_owned_torrent_request(
        db_session,
        user_id=owners[0].id,
        torrent_request_id=requests[0].id,
        retention_hours=36,
        now=cancelled_at,
    )

    assert first is not None and first.purge_scheduled
    assert first.purge_after == cancelled_at + timedelta(hours=36)
    assert replay is not None and not replay.cancelled and not replay.purge_scheduled
    assert torrent.state is ManagedTorrentState.READY
    assert torrent.progress == 1
    assert torrent.qb_state == "stalledup"
    assert torrent.purge_stop_pending is False
    assert torrent.desired_active is False
    assert torrent.desired_priority is None
    jobs = tuple((await db_session.scalars(select(TorrentJob))).all())
    assert len(jobs) == 1 and jobs[0].state is TorrentJobState.QUEUED


@pytest.mark.asyncio
async def test_reaper_expires_only_due_owner_and_keeps_other_subscription(
    db_session: AsyncSession,
) -> None:
    torrent, requests, owners = await _shared_ready(db_session, users=2)
    requests[0].unsubscribe_at = NOW
    requests[1].unsubscribe_at = NOW + timedelta(hours=1)
    await db_session.flush()
    results = await expire_ready_torrents_batch(db_session, now=NOW, retention_hours=24)

    assert len(results) == 1 and results[0].purge_job_id is None
    assert requests[0].state is TorrentRequestState.EXPIRED
    assert requests[0].expires_at == NOW
    assert requests[1].state is TorrentRequestState.READY
    assert torrent.state is ManagedTorrentState.READY
    first_usage = await db_session.get(UserStorageUsage, requests[0].user_id)
    second_usage = await db_session.get(UserStorageUsage, requests[1].user_id)
    assert first_usage is not None and first_usage.logical_bytes == 0
    assert second_usage is not None and second_usage.logical_bytes == 100


@pytest.mark.asyncio
async def test_reaper_last_due_owner_starts_grace_and_is_replay_safe(
    db_session: AsyncSession,
) -> None:
    torrent, requests, _ = await _shared_ready(db_session)
    torrent.qb_state = "uploading"
    requests[0].unsubscribe_at = NOW
    await db_session.flush()
    first = await expire_ready_torrents_batch(db_session, now=NOW, retention_hours=18)
    replay = await expire_ready_torrents_batch(
        db_session,
        now=NOW + timedelta(seconds=1),
        retention_hours=18,
    )

    assert len(first) == 1 and first[0].purge_job_id is not None
    assert replay == ()
    assert torrent.state is ManagedTorrentState.READY
    assert torrent.qb_state == "uploading"
    assert torrent.purge_stop_pending is False
    assert torrent.purge_after == NOW + timedelta(hours=18)
    assert await db_session.scalar(select(func.count()).select_from(TorrentJob)) == 1


@pytest.mark.asyncio
async def test_reaper_does_not_expire_future_deadline(db_session: AsyncSession) -> None:
    torrent, requests, _ = await _shared_ready(db_session)
    deadline = requests[0].unsubscribe_at
    assert deadline is not None
    assert (
        await expire_ready_torrents_batch(
            db_session,
            now=deadline - timedelta(seconds=1),
            retention_hours=48,
        )
        == ()
    )
    assert requests[0].state is TorrentRequestState.READY
    assert torrent.state is ManagedTorrentState.READY


@pytest.mark.asyncio
async def test_resubmit_at_deadline_expires_old_right_transactionally(
    db_session: AsyncSession,
) -> None:
    torrent, requests, owners = await _shared_ready(db_session, auto_hours=4)
    deadline = requests[0].unsubscribe_at
    assert deadline is not None
    renewed = await create_or_get_torrent_request(
        db_session,
        user_id=owners[0].id,
        info_hash=torrent.info_hash,
        name=torrent.name,
        total_size=torrent.total_size,
        now=deadline,
        auto_unsubscribe_hours=12,
    )

    assert requests[0].state is TorrentRequestState.EXPIRED
    assert requests[0].expires_at == deadline
    assert renewed.request_created and renewed.request.id != requests[0].id
    assert renewed.request.state is TorrentRequestState.READY
    assert renewed.request.unsubscribe_at == deadline + timedelta(hours=12)
    usage = await db_session.get(UserStorageUsage, owners[0].id)
    assert usage is not None and usage.logical_bytes == torrent.total_size


@pytest.mark.asyncio
async def test_resubscribe_during_purge_grace_reuses_content_and_cancels_job(
    db_session: AsyncSession,
) -> None:
    torrent, requests, owners = await _shared_ready(db_session)
    cancelled = await cancel_owned_torrent_request(
        db_session,
        user_id=owners[0].id,
        torrent_request_id=requests[0].id,
        retention_hours=24,
        now=NOW,
    )
    assert cancelled is not None and cancelled.purge_after is not None
    renewed = await create_or_get_torrent_request(
        db_session,
        user_id=owners[0].id,
        info_hash=torrent.info_hash,
        name=torrent.name,
        total_size=torrent.total_size,
        now=NOW + timedelta(hours=1),
        auto_unsubscribe_hours=48,
    )

    assert renewed.request_created and not renewed.managed_torrent_created
    assert not renewed.managed_torrent_reactivated
    assert torrent.state is ManagedTorrentState.READY and torrent.purge_after is None
    job = await db_session.scalar(select(TorrentJob))
    assert job is not None and job.state is TorrentJobState.CANCELLED


@pytest.mark.asyncio
async def test_resubscribe_at_purge_deadline_wins_before_activation(
    db_session: AsyncSession,
) -> None:
    torrent, requests, owners = await _shared_ready(db_session)
    cancelled = await cancel_owned_torrent_request(
        db_session,
        user_id=owners[0].id,
        torrent_request_id=requests[0].id,
        retention_hours=1,
        now=NOW,
    )
    assert cancelled is not None and cancelled.purge_after is not None
    renewed = await create_or_get_torrent_request(
        db_session,
        user_id=owners[0].id,
        info_hash=torrent.info_hash,
        name=torrent.name,
        total_size=torrent.total_size,
        now=cancelled.purge_after,
    )

    assert renewed.request_created is True
    assert torrent.state is ManagedTorrentState.READY
    assert torrent.purge_after is None
    purge_job = await db_session.scalar(select(TorrentJob))
    assert purge_job is not None and purge_job.state is TorrentJobState.CANCELLED


@pytest.mark.asyncio
async def test_legacy_ready_backfill_is_bounded_and_uses_rollout_time(
    db_session: AsyncSession,
) -> None:
    torrent, requests, _ = await _shared_ready(db_session, users=2)
    for request in requests:
        request.ready_at = NOW - timedelta(days=30)
        request.unsubscribe_at = None
    await db_session.flush()
    counts = (
        await backfill_ready_unsubscribe_deadlines_batch(
            db_session, now=NOW, auto_unsubscribe_hours=24, limit=1
        ),
        await backfill_ready_unsubscribe_deadlines_batch(
            db_session, now=NOW, auto_unsubscribe_hours=24, limit=1
        ),
        await backfill_ready_unsubscribe_deadlines_batch(
            db_session, now=NOW, auto_unsubscribe_hours=24, limit=2
        ),
    )

    assert counts == (1, 1, 0)
    assert all(request.unsubscribe_at == NOW + timedelta(hours=24) for request in requests)
    assert all(request.ready_at == NOW - timedelta(days=30) for request in requests)
    assert torrent.state is ManagedTorrentState.READY
