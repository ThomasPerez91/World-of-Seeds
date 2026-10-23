import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import maintenance
from app.maintenance import prune_stale_rows
from app.models import (
    Base,
    LoginThrottle,
    ManagedTorrent,
    TorrentJob,
    TorrentJobState,
    User,
    UserSession,
)

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'maintenance.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def _job(
    torrent: ManagedTorrent,
    name: str,
    *,
    job_type: str = "SYNC_TORRENT",
    state: TorrentJobState = TorrentJobState.COMPLETED,
    finished_ago: timedelta | None = None,
) -> TorrentJob:
    finished = None if finished_ago is None else _naive(NOW - finished_ago)
    return TorrentJob(
        managed_torrent=torrent,
        job_type=job_type,
        idempotency_key=f"{name}-{uuid.uuid4().hex}",
        state=state,
        finished_at=finished,
        available_at=_naive(NOW - timedelta(days=400)),
        max_attempts=5,
    )


@pytest.mark.asyncio
async def test_prune_keeps_recent_failed_and_add_jobs_but_removes_finished_noise(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(info_hash="a" * 40, name="pruned", total_size=1)
        session.add(torrent)
        jobs = {
            "old-complete": _job(torrent, "oc", finished_ago=timedelta(hours=2)),
            "recent-complete": _job(torrent, "rc", finished_ago=timedelta(minutes=10)),
            "old-cancelled": _job(
                torrent, "ox", state=TorrentJobState.CANCELLED, finished_ago=timedelta(hours=3)
            ),
            "old-failed": _job(
                torrent, "of", state=TorrentJobState.FAILED, finished_ago=timedelta(days=8)
            ),
            "recent-failed": _job(
                torrent, "rf", state=TorrentJobState.FAILED, finished_ago=timedelta(days=1)
            ),
            "old-add": _job(
                torrent, "add", job_type="ADD_TORRENT", finished_ago=timedelta(days=400)
            ),
            "old-purge": _job(
                torrent, "purge", job_type="PURGE_TORRENT", finished_ago=timedelta(days=400)
            ),
            "queued": _job(torrent, "q", state=TorrentJobState.QUEUED),
        }
        session.add_all(jobs.values())
        await session.flush()
        ids = {name: job.id for name, job in jobs.items()}

    result = await prune_stale_rows(sessions, now=NOW)

    assert result.jobs == 4
    async with sessions() as session:
        remaining = set((await session.scalars(select(TorrentJob.id))).all())
    assert remaining == {
        ids["recent-complete"],
        ids["recent-failed"],
        ids["old-add"],
        ids["queued"],
    }


@pytest.mark.asyncio
async def test_prune_removes_stale_sessions_and_unlocked_throttles_only(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session, session.begin():
        user = User(username="pruned-user", password_hash="hash")
        session.add(user)
        await session.flush()

        def user_session(
            name: str, *, expires: timedelta, revoked: timedelta | None = None
        ) -> UserSession:
            return UserSession(
                user_id=user.id,
                token_hash=name.ljust(64, "0"),
                csrf_token_hash=name.ljust(64, "1"),
                expires_at=NOW + expires,
                revoked_at=None if revoked is None else NOW + revoked,
            )

        keep_active = user_session("active", expires=timedelta(hours=1))
        keep_recent_expired = user_session("recent", expires=-timedelta(days=1))
        drop_expired = user_session("expired", expires=-timedelta(days=8))
        drop_revoked = user_session(
            "revoked", expires=timedelta(hours=1), revoked=-timedelta(days=8)
        )
        session.add_all([keep_active, keep_recent_expired, drop_expired, drop_revoked])
        session.add_all(
            [
                LoginThrottle(
                    key_hash="a" * 64, failures=2, window_started_at=NOW - timedelta(days=2)
                ),
                LoginThrottle(
                    key_hash="b" * 64,
                    failures=5,
                    window_started_at=NOW - timedelta(days=2),
                    locked_until=NOW + timedelta(minutes=5),
                ),
                LoginThrottle(
                    key_hash="c" * 64, failures=1, window_started_at=NOW - timedelta(hours=1)
                ),
            ]
        )
        await session.flush()
        kept = {keep_active.id, keep_recent_expired.id}

    result = await prune_stale_rows(sessions, now=NOW)

    assert (result.sessions, result.throttles) == (2, 1)
    async with sessions() as session:
        assert set((await session.scalars(select(UserSession.id))).all()) == kept
        assert set((await session.scalars(select(LoginThrottle.key_hash))).all()) == {
            "b" * 64,
            "c" * 64,
        }


@pytest.mark.asyncio
async def test_prune_absorbs_a_backlog_in_batches_and_honors_the_time_budget(
    sessions: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(maintenance, "BATCH", 2)
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(info_hash="b" * 40, name="backlog", total_size=1)
        session.add(torrent)
        session.add_all(
            _job(torrent, f"job{index}", finished_ago=timedelta(hours=5)) for index in range(5)
        )

    # No budget left: exactly one batch per statement.
    first = await prune_stale_rows(sessions, now=NOW, budget_seconds=-1)
    assert first.jobs == 2

    second = await prune_stale_rows(sessions, now=NOW)
    assert second.jobs == 3
    async with sessions() as session:
        assert (await session.scalars(select(TorrentJob.id))).all() == []
