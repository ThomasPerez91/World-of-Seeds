from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.coordination import RedisCoordinator
from app.jobs.torrent_effects import TorrentRetentionReaper
from app.models import (
    Base,
    ManagedTorrent,
    ManagedTorrentState,
    StorageLedger,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    User,
    UserStorageUsage,
)
from app.torrents import (
    TorrentPurgeInProgressError,
    cancel_owned_torrent_request,
    create_or_get_torrent_request,
    expire_ready_torrents_batch,
    extend_ready_torrent_retention,
    retention_days_for_popularity,
)

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.parametrize(
    ("users", "days"),
    [(1, 5), (2, 6), (3, 6), (4, 7), (5, 7), (6, 8), (7, 8), (8, 9), (9, 9), (10, 10), (50, 10)],
)
def test_retention_days_follow_distinct_user_popularity(users: int, days: int) -> None:
    assert retention_days_for_popularity(users) == days


async def _user(session: AsyncSession, name: str) -> User:
    user = User(username=name, password_hash="test-password-hash")
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_history_counts_cancelled_but_not_duplicate_requests_and_never_shortens(
    db_session: AsyncSession,
) -> None:
    users = [await _user(db_session, f"retention-{index}") for index in range(4)]
    first = await create_or_get_torrent_request(
        db_session,
        user_id=users[0].id,
        info_hash="1" * 40,
        name="Popular content",
        total_size=100,
        now=NOW,
    )
    second = await create_or_get_torrent_request(
        db_session,
        user_id=users[1].id,
        info_hash="1" * 40,
        name="Popular content",
        total_size=100,
        now=NOW,
    )
    first.managed_torrent.state = ManagedTorrentState.READY
    first.managed_torrent.progress = 1
    first.request.state = second.request.state = TorrentRequestState.READY
    await extend_ready_torrent_retention(db_session, first.managed_torrent, now=NOW)
    assert first.managed_torrent.retention_expires_at == NOW + timedelta(days=6)

    cancelled = await cancel_owned_torrent_request(
        db_session,
        user_id=users[0].id,
        torrent_request_id=first.request.id,
        retention_hours=48,
        now=NOW + timedelta(hours=1),
    )
    assert cancelled is not None and cancelled.purge_scheduled is False
    repeated = await create_or_get_torrent_request(
        db_session,
        user_id=users[0].id,
        info_hash="1" * 40,
        name="Popular content",
        total_size=100,
        now=NOW + timedelta(hours=2),
    )
    assert repeated.request_created is True
    assert first.managed_torrent.retention_expires_at == NOW + timedelta(days=6)

    for index in (2, 3):
        await create_or_get_torrent_request(
            db_session,
            user_id=users[index].id,
            info_hash="1" * 40,
            name="Popular content",
            total_size=100,
            now=NOW + timedelta(hours=3 + index),
        )
    assert first.managed_torrent.retention_expires_at == NOW + timedelta(days=7)
    assert (
        await db_session.scalar(
            select(func.count(func.distinct(TorrentRequest.user_id))).where(
                TorrentRequest.managed_torrent_id == first.managed_torrent.id
            )
        )
        == 4
    )

    first.managed_torrent.retention_expires_at = NOW + timedelta(days=10)
    await extend_ready_torrent_retention(
        db_session,
        first.managed_torrent,
        now=NOW + timedelta(days=1),
    )
    assert first.managed_torrent.retention_expires_at == NOW + timedelta(days=10)


@pytest.mark.asyncio
async def test_request_one_second_before_expiry_extends_but_exact_or_late_is_rejected(
    db_session: AsyncSession,
) -> None:
    first_user = await _user(db_session, "boundary-first")
    second_user = await _user(db_session, "boundary-second")
    late_user = await _user(db_session, "boundary-late")
    first = await create_or_get_torrent_request(
        db_session,
        user_id=first_user.id,
        info_hash="2" * 40,
        name="Boundary content",
        total_size=50,
        now=NOW,
    )
    first.managed_torrent.state = ManagedTorrentState.READY
    first.managed_torrent.progress = 1
    first.request.state = TorrentRequestState.READY
    await extend_ready_torrent_retention(db_session, first.managed_torrent, now=NOW)
    original_expiry = NOW + timedelta(days=5)
    assert first.managed_torrent.retention_expires_at == original_expiry

    near = await create_or_get_torrent_request(
        db_session,
        user_id=second_user.id,
        info_hash="2" * 40,
        name="Boundary content",
        total_size=50,
        now=original_expiry - timedelta(seconds=1),
    )
    assert near.request.state is TorrentRequestState.READY
    extended_expiry = NOW + timedelta(days=6)
    assert first.managed_torrent.retention_expires_at == extended_expiry

    with pytest.raises(TorrentPurgeInProgressError):
        await create_or_get_torrent_request(
            db_session,
            user_id=late_user.id,
            info_hash="2" * 40,
            name="Boundary content",
            total_size=50,
            now=extended_expiry,
        )
    with pytest.raises(TorrentPurgeInProgressError):
        await create_or_get_torrent_request(
            db_session,
            user_id=late_user.id,
            info_hash="2" * 40,
            name="Boundary content",
            total_size=50,
            now=extended_expiry + timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_manual_purge_pending_cannot_reactivate_after_ready_expiration(
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session, "expired-manual-reactivation")
    created = await create_or_get_torrent_request(
        db_session,
        user_id=owner.id,
        info_hash="a" * 40,
        name="Expired manual retention",
        total_size=25,
        now=NOW,
    )
    created.managed_torrent.state = ManagedTorrentState.READY
    created.managed_torrent.progress = 1
    created.request.state = TorrentRequestState.READY
    await extend_ready_torrent_retention(db_session, created.managed_torrent, now=NOW)
    expires_at = NOW + timedelta(days=5)
    cancelled = await cancel_owned_torrent_request(
        db_session,
        user_id=owner.id,
        torrent_request_id=created.request.id,
        retention_hours=48,
        now=expires_at - timedelta(hours=1),
    )
    assert cancelled is not None and cancelled.purge_after is not None
    assert cancelled.purge_after > expires_at

    with pytest.raises(TorrentPurgeInProgressError):
        await create_or_get_torrent_request(
            db_session,
            user_id=owner.id,
            info_hash="a" * 40,
            name="Expired manual retention",
            total_size=25,
            now=expires_at,
        )


async def _seed_due_torrent(
    sessions: async_sessionmaker[AsyncSession],
    *,
    suffix: str,
    expires_at: datetime,
    users: int = 2,
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...]]:
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(
            info_hash=(suffix * 40)[:40],
            name=f"Due {suffix}",
            total_size=25,
            state=ManagedTorrentState.READY,
            progress=1,
            ready_at=NOW,
            retention_expires_at=expires_at,
        )
        session.add(torrent)
        await session.flush()
        request_ids: list[uuid.UUID] = []
        for index in range(users):
            user = User(
                username=f"due-{suffix}-{index}",
                password_hash="test-password-hash",
            )
            request = TorrentRequest(
                user=user,
                managed_torrent=torrent,
                state=TorrentRequestState.READY,
                ready_at=NOW,
            )
            session.add_all([request, UserStorageUsage(user=user, logical_bytes=25)])
            await session.flush()
            request_ids.append(request.id)
        return torrent.id, tuple(request_ids)


@pytest.mark.asyncio
async def test_expiration_boundary_is_atomic_accounted_and_replay_safe(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    expires_at = NOW + timedelta(days=5)
    torrent_id, request_ids = await _seed_due_torrent(
        sessions,
        suffix="3",
        expires_at=expires_at,
    )
    async with sessions() as session, session.begin():
        assert (
            await expire_ready_torrents_batch(
                session,
                now=expires_at - timedelta(seconds=1),
            )
            == ()
        )

    async with sessions() as session, session.begin():
        expired = await expire_ready_torrents_batch(session, now=expires_at)
        assert len(expired) == 1
        assert {item.request_id for item in expired[0].requests} == set(request_ids)

    async with sessions() as session:
        torrent = await session.get(ManagedTorrent, torrent_id)
        requests = tuple(
            (
                await session.scalars(
                    select(TorrentRequest).where(TorrentRequest.id.in_(request_ids))
                )
            ).all()
        )
        usages = tuple((await session.scalars(select(UserStorageUsage))).all())
        jobs = tuple((await session.scalars(select(TorrentJob))).all())
        assert torrent is not None and torrent.state is ManagedTorrentState.PURGE_PENDING
        assert torrent.purge_after == expires_at.replace(tzinfo=None)
        assert torrent.desired_active is False
        assert torrent.desired_priority is None
        assert torrent.purge_stop_pending is True
        assert all(request.state is TorrentRequestState.EXPIRED for request in requests)
        assert all(request.expires_at == expires_at.replace(tzinfo=None) for request in requests)
        assert all(usage.logical_bytes == 0 for usage in usages)
        assert len(jobs) == 1
        assert jobs[0].job_type == "PURGE_TORRENT"
        assert jobs[0].state is TorrentJobState.QUEUED

    async with sessions() as session, session.begin():
        assert (
            await expire_ready_torrents_batch(
                session,
                now=expires_at + timedelta(seconds=1),
            )
            == ()
        )
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TorrentJob)) == 1


@pytest.mark.asyncio
async def test_reaper_restart_and_redis_loss_cannot_lose_expiration(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    expires_at = NOW + timedelta(days=5)
    await _seed_due_torrent(sessions, suffix="4", expires_at=expires_at, users=1)
    before_restart = TorrentRetentionReaper(
        sessions,
        RedisCoordinator.unconfigured(),
        clock=lambda: expires_at - timedelta(seconds=1),
    )
    assert await before_restart.expire_once() == 0

    after_restart = TorrentRetentionReaper(
        sessions,
        RedisCoordinator.unconfigured(),
        clock=lambda: expires_at,
    )
    assert await after_restart.expire_once() == 1
    assert await after_restart.expire_once() == 0
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TorrentJob)) == 1


@pytest.mark.asyncio
async def test_expiration_scan_is_bounded_and_progresses_across_batches(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    for suffix in ("5", "6", "7"):
        await _seed_due_torrent(sessions, suffix=suffix, expires_at=NOW, users=1)
    async with sessions() as session, session.begin():
        assert len(await expire_ready_torrents_batch(session, now=NOW, limit=2)) == 2
    async with sessions() as session, session.begin():
        assert len(await expire_ready_torrents_batch(session, now=NOW, limit=2)) == 1


@pytest.mark.asyncio
async def test_postgresql_request_expiration_race_is_deterministic() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL retention race test requires WOS_DATABASE_URL")

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:12]
    info_hash = (suffix + "0" * 40)[:40]
    user_ids: list[uuid.UUID] = []
    torrent_id: uuid.UUID | None = None
    try:
        async with factory() as session, session.begin():
            users = [
                User(username=f"retention-race-{suffix}-{index}", password_hash="hash")
                for index in range(2)
            ]
            torrent = ManagedTorrent(
                info_hash=info_hash,
                name="Retention race",
                total_size=10,
                state=ManagedTorrentState.READY,
                progress=1,
                ready_at=NOW - timedelta(days=5),
                retention_expires_at=NOW,
            )
            session.add_all([*users, torrent])
            await session.flush()
            user_ids = [user.id for user in users]
            torrent_id = torrent.id
            session.add_all(
                [
                    TorrentRequest(
                        user_id=users[0].id,
                        managed_torrent_id=torrent.id,
                        state=TorrentRequestState.READY,
                    ),
                    UserStorageUsage(user_id=users[0].id, logical_bytes=10),
                ]
            )

        async def request_at_deadline() -> str:
            async with factory() as session, session.begin():
                with pytest.raises(TorrentPurgeInProgressError):
                    await create_or_get_torrent_request(
                        session,
                        user_id=user_ids[1],
                        info_hash=info_hash,
                        name="Retention race",
                        total_size=10,
                        now=NOW,
                    )
            return "rejected"

        async def expire_at_deadline() -> int:
            async with factory() as session, session.begin():
                return len(await expire_ready_torrents_batch(session, now=NOW))

        request_result, first_expiration = await asyncio.gather(
            request_at_deadline(),
            expire_at_deadline(),
        )
        assert request_result == "rejected"
        assert first_expiration in {0, 1}
        async with factory() as session, session.begin():
            replay_expiration = len(await expire_ready_torrents_batch(session, now=NOW))
        assert first_expiration + replay_expiration == 1
    finally:
        async with factory() as session, session.begin():
            if torrent_id is not None:
                await session.execute(delete(ManagedTorrent).where(ManagedTorrent.id == torrent_id))
                ledger = await session.get(StorageLedger, 1, with_for_update=True)
                if ledger is not None:
                    ledger.managed_bytes = max(0, ledger.managed_bytes - 10)
            if user_ids:
                await session.execute(delete(User).where(User.id.in_(user_ids)))
        await engine.dispose()
