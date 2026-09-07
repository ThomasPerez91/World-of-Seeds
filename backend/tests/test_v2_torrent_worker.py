import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.coordination import RedisCoordinator
from app.jobs.worker import (
    PermanentTorrentJobError,
    TorrentJobHandler,
    TorrentJobSnapshot,
    TorrentWorker,
    TorrentWorkerConfig,
    TransientTorrentJobError,
)
from app.models import Base, ManagedTorrent, ManagedTorrentState, TorrentJob, TorrentJobState

NOW = datetime(2026, 8, 21, 18, tzinfo=UTC)
CONFIG = TorrentWorkerConfig(
    poll_interval=timedelta(milliseconds=10),
    claim_ttl=timedelta(milliseconds=300),
    execution_timeout=timedelta(seconds=2),
    recovery_interval=timedelta(milliseconds=100),
    retry_base=timedelta(seconds=3),
    retry_max=timedelta(seconds=30),
    shutdown_grace=timedelta(seconds=1),
)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@pytest_asyncio.fixture
async def worker_sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()


async def create_job(
    sessions: async_sessionmaker[AsyncSession],
    *,
    job_type: str = "TEST_JOB",
    state: TorrentJobState = TorrentJobState.QUEUED,
    claimed_by: str | None = None,
    claim_expires_at: datetime | None = None,
    timeout_at: datetime | None = None,
    available_at: datetime = NOW,
) -> uuid.UUID:
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(
            info_hash=uuid.uuid4().hex + "00000000",
            name="Worker test torrent",
            total_size=1,
        )
        job = TorrentJob(
            managed_torrent=torrent,
            job_type=job_type,
            idempotency_key=f"test-{uuid.uuid4().hex}",
            state=state,
            available_at=available_at,
            attempt_count=1 if state is TorrentJobState.RUNNING else 0,
            claimed_by=claimed_by,
            claim_expires_at=claim_expires_at,
            timeout_at=timeout_at,
        )
        session.add(job)
        await session.flush()
        return job.id


async def load_job(sessions: async_sessionmaker[AsyncSession], job_id: uuid.UUID) -> TorrentJob:
    async with sessions() as session:
        job = await session.get(TorrentJob, job_id)
        assert job is not None
        return job


def worker(
    sessions: async_sessionmaker[AsyncSession],
    handler: TorrentJobHandler,
    *,
    now: datetime = NOW,
    config: TorrentWorkerConfig = CONFIG,
) -> TorrentWorker:
    return TorrentWorker(
        sessions,
        RedisCoordinator.unconfigured(),
        {"TEST_JOB": handler},
        worker_id="test-worker",
        config=config,
        clock=lambda: now,
        jitter=lambda lower, _upper: lower,
    )


@pytest.mark.asyncio
async def test_worker_completes_a_supported_job_once(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await create_job(worker_sessions)
    handled: list[TorrentJobSnapshot] = []

    async def handler(snapshot: TorrentJobSnapshot) -> None:
        handled.append(snapshot)

    assert await worker(worker_sessions, handler).process_once() is True

    job = await load_job(worker_sessions, job_id)
    assert job.state is TorrentJobState.COMPLETED
    assert job.attempt_count == 1
    assert job.claimed_by is None
    assert [snapshot.id for snapshot in handled] == [job_id]
    assert handled[0].idempotency_key == job.idempotency_key


@pytest.mark.asyncio
async def test_worker_retries_transient_failure_with_bounded_backoff(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await create_job(worker_sessions)

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        raise TransientTorrentJobError("integration_unavailable")

    await worker(worker_sessions, handler).process_once()

    job = await load_job(worker_sessions, job_id)
    assert job.state is TorrentJobState.QUEUED
    assert as_utc(job.available_at) == NOW + CONFIG.retry_base
    assert job.last_error_code == "integration_unavailable"
    assert job.claimed_by is None


@pytest.mark.asyncio
async def test_worker_persists_managed_retry_state_with_job_backoff(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await create_job(worker_sessions)

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        raise TransientTorrentJobError(
            "integration_unavailable",
            torrent_state=ManagedTorrentState.RETRY_WAIT,
        )

    await worker(worker_sessions, handler).process_once()

    job = await load_job(worker_sessions, job_id)
    async with worker_sessions() as session:
        torrent = await session.get(ManagedTorrent, job.managed_torrent_id)
        assert torrent is not None
        assert torrent.state is ManagedTorrentState.RETRY_WAIT
        assert torrent.retry_at is not None
        assert as_utc(torrent.retry_at) == NOW + CONFIG.retry_base


@pytest.mark.asyncio
async def test_worker_fails_permanent_error_without_retry(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await create_job(worker_sessions)

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        raise PermanentTorrentJobError("invalid_job_payload")

    await worker(worker_sessions, handler).process_once()

    job = await load_job(worker_sessions, job_id)
    assert job.state is TorrentJobState.FAILED
    assert job.last_error_code == "invalid_job_payload"
    assert job.finished_at is not None
    assert as_utc(job.finished_at) == NOW


@pytest.mark.asyncio
async def test_worker_does_not_claim_an_unsupported_future_job(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await create_job(worker_sessions, job_type="ADD_TORRENT")

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        raise AssertionError("unsupported job must not execute")

    assert await worker(worker_sessions, handler).process_once() is False

    job = await load_job(worker_sessions, job_id)
    assert job.state is TorrentJobState.QUEUED
    assert job.attempt_count == 0


@pytest.mark.asyncio
async def test_worker_recovers_an_abandoned_claim_before_polling(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await create_job(
        worker_sessions,
        job_type="FUTURE_JOB",
        state=TorrentJobState.RUNNING,
        claimed_by="dead-worker",
        claim_expires_at=NOW - timedelta(seconds=1),
        timeout_at=NOW + timedelta(minutes=1),
    )

    runtime = TorrentWorker(
        worker_sessions,
        RedisCoordinator.unconfigured(),
        {},
        worker_id="test-worker",
        config=CONFIG,
        clock=lambda: NOW,
    )
    assert await runtime.process_once() is False

    job = await load_job(worker_sessions, job_id)
    assert job.state is TorrentJobState.QUEUED
    assert as_utc(job.available_at) == NOW + CONFIG.retry_base
    assert job.last_error_code == "claim_expired"


@pytest.mark.asyncio
async def test_graceful_stop_finishes_in_flight_job(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await create_job(worker_sessions)
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        started.set()
        await release.wait()

    runtime = worker(worker_sessions, handler)
    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=1)
    runtime.request_stop()
    release.set()
    await asyncio.wait_for(run_task, timeout=1)

    assert (await load_job(worker_sessions, job_id)).state is TorrentJobState.COMPLETED


@pytest.mark.asyncio
async def test_completed_job_wakes_loop_without_waiting_for_poll_interval(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first_id = await create_job(worker_sessions)
    second_id = await create_job(worker_sessions)
    handled = 0
    slow_poll = TorrentWorkerConfig(
        poll_interval=timedelta(seconds=10),
        claim_ttl=CONFIG.claim_ttl,
        execution_timeout=CONFIG.execution_timeout,
        recovery_interval=CONFIG.recovery_interval,
        retry_base=CONFIG.retry_base,
        retry_max=CONFIG.retry_max,
        shutdown_grace=CONFIG.shutdown_grace,
    )

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        nonlocal handled
        handled += 1
        if handled == 2:
            runtime.request_stop()

    runtime = worker(worker_sessions, handler, config=slow_poll)
    await asyncio.wait_for(runtime.run(), timeout=1)

    assert (await load_job(worker_sessions, first_id)).state is TorrentJobState.COMPLETED
    assert (await load_job(worker_sessions, second_id)).state is TorrentJobState.COMPLETED


@pytest.mark.asyncio
async def test_forced_stop_leaves_claim_for_durable_recovery(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await create_job(worker_sessions)
    started = asyncio.Event()
    short_grace = TorrentWorkerConfig(
        poll_interval=CONFIG.poll_interval,
        claim_ttl=CONFIG.claim_ttl,
        execution_timeout=CONFIG.execution_timeout,
        recovery_interval=CONFIG.recovery_interval,
        retry_base=CONFIG.retry_base,
        retry_max=CONFIG.retry_max,
        shutdown_grace=timedelta(milliseconds=10),
    )

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        started.set()
        await asyncio.Event().wait()

    runtime = worker(worker_sessions, handler, config=short_grace)
    run_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(started.wait(), timeout=1)
    runtime.request_stop()
    await asyncio.wait_for(run_task, timeout=1)

    job = await load_job(worker_sessions, job_id)
    assert job.state is TorrentJobState.RUNNING
    assert job.claimed_by == "test-worker"
    assert job.claim_expires_at is not None


@pytest.mark.asyncio
async def test_claim_heartbeat_keeps_long_job_owned(
    worker_sessions: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await create_job(worker_sessions)
    clock = [NOW]
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        started.set()
        await release.wait()

    runtime = TorrentWorker(
        worker_sessions,
        RedisCoordinator.unconfigured(),
        {"TEST_JOB": handler},
        worker_id="test-worker",
        config=CONFIG,
        clock=lambda: clock[0],
    )
    run_task = asyncio.create_task(runtime.process_once())
    await asyncio.wait_for(started.wait(), timeout=1)
    first_expiry = (await load_job(worker_sessions, job_id)).claim_expires_at
    clock[0] += timedelta(milliseconds=200)
    await asyncio.sleep(0.12)
    renewed_expiry = (await load_job(worker_sessions, job_id)).claim_expires_at
    assert first_expiry is not None
    assert renewed_expiry is not None
    assert renewed_expiry > first_expiry

    release.set()
    await asyncio.wait_for(run_task, timeout=1)


@pytest.mark.asyncio
async def test_postgresql_worker_renews_and_completes_durable_job() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL worker test requires WOS_DATABASE_URL")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    managed_id: uuid.UUID | None = None
    try:
        job_id = await create_job(
            sessions,
            job_type="WORKER_TEST",
            available_at=datetime.now(UTC),
        )
        async with sessions() as session:
            created = await session.get(TorrentJob, job_id)
            assert created is not None
            managed_id = created.managed_torrent_id

        async def handler(_snapshot: TorrentJobSnapshot) -> None:
            await asyncio.sleep(0.12)

        runtime = TorrentWorker(
            sessions,
            RedisCoordinator.unconfigured(),
            {"WORKER_TEST": handler},
            worker_id=f"postgres-worker-{uuid.uuid4().hex[:8]}",
            config=CONFIG,
        )
        assert await runtime.process_once() is True
        assert (await load_job(sessions, job_id)).state is TorrentJobState.COMPLETED
    finally:
        if managed_id is not None:
            async with sessions() as session, session.begin():
                await session.execute(delete(ManagedTorrent).where(ManagedTorrent.id == managed_id))
        await engine.dispose()
