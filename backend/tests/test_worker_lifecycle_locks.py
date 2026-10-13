import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_storage_admission_concurrency import postgres_storage as postgres_storage
from test_v2_torrent_api import RecordingRedis
from test_v2_torrent_worker import NOW, create_job, worker
from test_v2_torrent_worker import worker_sessions as worker_sessions

from app.coordination import RedisCoordinator
from app.jobs.torrent_jobs import (
    TorrentJobTransitionError,
    renew_torrent_job_claim,
    request_torrent_job_cancellation,
)
from app.jobs.worker import TorrentJobClaimLostError, TorrentJobSnapshot, TorrentWorker
from app.models import ManagedTorrent, ManagedTorrentState, TorrentJob, TorrentJobState


@pytest.mark.asyncio
@pytest.mark.parametrize("permanent", [False, True])
@pytest.mark.parametrize(
    "state",
    [
        ManagedTorrentState.PURGE_PENDING,
        ManagedTorrentState.PURGING,
        ManagedTorrentState.PURGED,
        ManagedTorrentState.READY,
    ],
)
async def test_cancelled_failure_preserves_the_newer_lifecycle(
    worker_sessions: async_sessionmaker[AsyncSession],
    permanent: bool,
    state: ManagedTorrentState,
) -> None:
    job_id = await create_job(
        worker_sessions,
        state=TorrentJobState.RUNNING,
        claimed_by="test-worker",
        claim_expires_at=NOW + timedelta(minutes=1),
        timeout_at=NOW + timedelta(minutes=2),
    )
    async with worker_sessions() as session, session.begin():
        job = await session.get(TorrentJob, job_id)
        assert job is not None
        snapshot = TorrentJobSnapshot.from_model(job)
        job.cancel_requested_at = NOW
        torrent = await session.get(ManagedTorrent, job.managed_torrent_id)
        assert torrent is not None
        torrent.state = state
        torrent.lifecycle_generation = 5
        torrent.progress = 1 if state is ManagedTorrentState.READY else 0
        torrent.ready_at = NOW if state is ManagedTorrentState.READY else None
        torrent.purge_after = (
            None if state is ManagedTorrentState.PURGED else NOW + timedelta(hours=1)
        )
        torrent.updated_at = NOW - timedelta(seconds=1)
        expected = (
            torrent.state,
            torrent.lifecycle_generation,
            torrent.progress,
            torrent.retry_at,
            torrent.purge_after,
            torrent.updated_at,
        )

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        pass

    await worker(worker_sessions, handler)._finish_failure(
        snapshot,
        "integration_unavailable",
        permanent=permanent,
        torrent_state=ManagedTorrentState.RETRY_WAIT,
    )
    async with worker_sessions() as session:
        job = await session.get(TorrentJob, job_id)
        torrent = await session.get(ManagedTorrent, snapshot.managed_torrent_id)
        assert job is not None and torrent is not None
        assert job.state is TorrentJobState.CANCELLED
        assert job.claimed_by is None and job.claim_expires_at is None
        # SQLite returns timestamps without a timezone; compare the durable fields in UTC.
        actual = (
            torrent.state,
            torrent.lifecycle_generation,
            torrent.progress,
            torrent.retry_at,
            torrent.purge_after,
            torrent.updated_at,
        )

        def normalize(values: tuple[object, ...]) -> tuple[object, ...]:
            return tuple(
                value.replace(tzinfo=UTC) if isinstance(value, datetime) else value
                for value in values
            )

        assert normalize(actual) == normalize(expected)


async def seed_running_job(
    sessions: async_sessionmaker[AsyncSession],
    prefix: str,
    now: datetime,
) -> TorrentJobSnapshot:
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(
            id=uuid.uuid4(),
            name=prefix,
            info_hash=uuid.uuid4().hex + "00000000",
            total_size=1,
        )
        session.add(torrent)
        await session.flush()
        job = TorrentJob(
            managed_torrent_id=torrent.id,
            job_type="ADD_TORRENT",
            idempotency_key=f"lock:{torrent.id}",
            state=TorrentJobState.RUNNING,
            claimed_by="lock-worker",
            attempt_count=1,
            available_at=now,
            claim_expires_at=now + timedelta(minutes=5),
            timeout_at=now + timedelta(minutes=10),
        )
        session.add(job)
        await session.flush()
        return TorrentJobSnapshot.from_model(job)


@pytest.mark.asyncio
@pytest.mark.parametrize("permanent", [False, True])
async def test_failure_waiting_for_parent_allows_heartbeat_and_purge_cancellation(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
    permanent: bool,
) -> None:
    sessions, prefix = postgres_storage
    now = datetime.now(UTC)
    snapshot = await seed_running_job(sessions, prefix, now)
    redis = RecordingRedis()
    instance = TorrentWorker(
        sessions,
        cast(RedisCoordinator, redis),
        {},
        worker_id="lock-worker",
        clock=lambda: now,
    )
    entered = asyncio.Event()
    original_get = AsyncSession.get
    task = None

    async def get(session: AsyncSession, entity: Any, *args: Any, **kwargs: Any) -> Any:
        if asyncio.current_task() is task and entity is ManagedTorrent:
            entered.set()
        return await original_get(session, entity, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", get)
    try:
        async with sessions() as lifecycle, lifecycle.begin():
            torrent = await lifecycle.get(
                ManagedTorrent, snapshot.managed_torrent_id, with_for_update=True
            )
            assert torrent is not None
            task = asyncio.create_task(
                instance._finish_failure(
                    snapshot,
                    "integration_unavailable",
                    permanent=permanent,
                    torrent_state=ManagedTorrentState.RETRY_WAIT,
                )
            )
            async with asyncio.timeout(2):
                await entered.wait()
            # While finalization waits for the parent, its job remains available
            # to a real independent heartbeat transaction.
            async with sessions() as heartbeat, heartbeat.begin():
                async with asyncio.timeout(1):
                    job = await heartbeat.get(TorrentJob, snapshot.id, with_for_update=True)
                    assert job is not None
                    await renew_torrent_job_claim(
                        heartbeat,
                        job,
                        worker_id="lock-worker",
                        now=now,
                        claim_ttl=timedelta(minutes=5),
                    )
            # This is the torrent->job lock sequence used by purge activation.
            torrent.state = ManagedTorrentState.PURGE_PENDING
            torrent.lifecycle_generation += 1
            torrent.purge_after = now
            async with asyncio.timeout(2):
                cancelled = await request_torrent_job_cancellation(lifecycle, snapshot.id, now=now)
                assert cancelled is not None and cancelled.cancel_requested_at is not None
        async with asyncio.timeout(2):
            await task
        async with sessions() as session:
            job = await session.get(TorrentJob, snapshot.id)
            torrent = await session.get(ManagedTorrent, snapshot.managed_torrent_id)
            assert job is not None and torrent is not None
            assert job.state is TorrentJobState.CANCELLED
            assert torrent.state is ManagedTorrentState.PURGE_PENDING
            assert torrent.retry_at is None
        assert redis.events == [] and redis.queue_events == []
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["expired", "new_attempt"])
async def test_failure_revalidates_the_claim_after_waiting_for_parent(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    sessions, prefix = postgres_storage
    now = datetime.now(UTC)
    snapshot = await seed_running_job(sessions, prefix, now)
    clock = [now]
    redis = RecordingRedis()
    instance = TorrentWorker(
        sessions,
        cast(RedisCoordinator, redis),
        {},
        worker_id="lock-worker",
        clock=lambda: clock[0],
    )
    entered = asyncio.Event()
    original_get = AsyncSession.get
    task = None

    async def get(session: AsyncSession, entity: Any, *args: Any, **kwargs: Any) -> Any:
        if asyncio.current_task() is task and entity is ManagedTorrent:
            entered.set()
        return await original_get(session, entity, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", get)
    try:
        async with sessions() as blocker, blocker.begin():
            assert (
                await blocker.get(ManagedTorrent, snapshot.managed_torrent_id, with_for_update=True)
                is not None
            )
            task = asyncio.create_task(
                instance._finish_failure(
                    snapshot,
                    "integration_unavailable",
                    permanent=True,
                    torrent_state=ManagedTorrentState.ERROR,
                )
            )
            async with asyncio.timeout(2):
                await entered.wait()
            async with sessions() as changed, changed.begin():
                async with asyncio.timeout(1):
                    job = await changed.get(TorrentJob, snapshot.id, with_for_update=True)
                    assert job is not None
                    if change == "expired":
                        job.claim_expires_at = now + timedelta(seconds=1)
                        clock[0] = now + timedelta(seconds=2)
                    else:
                        job.attempt_count += 1
        with pytest.raises((TorrentJobTransitionError, TorrentJobClaimLostError)):
            await task
        async with sessions() as session:
            job = await session.get(TorrentJob, snapshot.id)
            torrent = await session.get(ManagedTorrent, snapshot.managed_torrent_id)
            assert job is not None and torrent is not None
            assert job.state is TorrentJobState.RUNNING
            assert torrent.state is ManagedTorrentState.PENDING
        assert redis.events == [] and redis.queue_events == []
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
