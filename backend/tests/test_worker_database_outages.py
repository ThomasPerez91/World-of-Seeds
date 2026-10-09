import asyncio
import socket
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.coordination import RedisCoordinator
from app.jobs.torrent_effects import TorrentRetentionReaper, TorrentSyncEnqueuer
from app.jobs.worker import TorrentJobSnapshot, TorrentWorker, TorrentWorkerConfig
from app.models import Base, ManagedTorrent, TorrentJob, TorrentJobState

NOW = datetime(2026, 10, 9, tzinfo=UTC)
CONFIG = TorrentWorkerConfig(
    poll_interval=timedelta(seconds=0.03),
    claim_ttl=timedelta(seconds=0.3),
    execution_timeout=timedelta(seconds=2),
    recovery_interval=timedelta(seconds=0.03),
    retry_base=timedelta(seconds=0.03),
    shutdown_grace=timedelta(seconds=1),
)


class OutageSessions(async_sessionmaker[AsyncSession]):
    def __init__(
        self,
        healthy: async_sessionmaker[AsyncSession],
        unavailable: async_sessionmaker[AsyncSession],
    ) -> None:
        super().__init__(expire_on_commit=False)
        self.healthy = healthy
        self.unavailable = unavailable
        self.failures_left = 0
        self.calls: list[float] = []

    def __call__(self, **local_kw: Any) -> AsyncSession:
        self.calls.append(monotonic())
        if self.failures_left:
            self.failures_left -= 1
            return self.unavailable(**local_kw)
        return self.healthy(**local_kw)


@pytest_asyncio.fixture
async def outage_sessions(tmp_path: Path) -> AsyncIterator[OutageSessions]:
    healthy = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outage.db'}")
    async with healthy.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        # A reserved port without listen() produces a real raw asyncpg connection refusal.
        unavailable = create_async_engine(
            f"postgresql+asyncpg://FAKE_USER:FAKE_PRIVATE_PASSWORD@127.0.0.1:{port}/FAKE_DATABASE"
        )
        try:
            yield OutageSessions(
                async_sessionmaker(healthy, expire_on_commit=False),
                async_sessionmaker(unavailable, expire_on_commit=False),
            )
        finally:
            await unavailable.dispose()
            await healthy.dispose()


async def add_job(sessions: OutageSessions) -> TorrentJob:
    async with sessions.healthy() as session, session.begin():
        torrent = ManagedTorrent(
            info_hash=uuid4().hex + "00000000", name="Outage test", total_size=1
        )
        job = TorrentJob(
            managed_torrent=torrent,
            job_type="OUTAGE_TEST",
            idempotency_key=f"outage-{uuid4().hex}",
            available_at=NOW,
        )
        session.add(job)
        await session.flush()
        return job


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["recover", "claim"])
async def test_raw_connection_failure_is_nonfatal_for_worker_polling(
    outage_sessions: OutageSessions,
    caplog: pytest.LogCaptureFixture,
    phase: str,
) -> None:
    async def unused(_snapshot: TorrentJobSnapshot) -> None:
        pytest.fail("Polling must not execute a handler")

    worker = TorrentWorker(
        outage_sessions,
        RedisCoordinator.unconfigured(),
        {"OUTAGE_TEST": unused},
        worker_id="outage-worker",
        config=CONFIG,
        clock=lambda: NOW,
    )
    outage_sessions.failures_left = 1
    if phase == "recover":
        await worker._recover_if_due(force=True)
        assert worker._last_recovery is None
    else:
        assert await worker._claim_one() is None
    assert "torrent_worker_database_unavailable" in caplog.text
    assert "FAKE_PRIVATE_PASSWORD" not in caplog.text
    assert "Connect call failed" not in caplog.text


@pytest.mark.asyncio
async def test_worker_resumes_after_outage_without_cancelling_taskgroup_sibling(
    outage_sessions: OutageSessions,
) -> None:
    job = await add_job(outage_sessions)
    outage_sessions.calls.clear()
    outage_sessions.failures_left = 2
    finished = asyncio.Event()
    handled = 0
    sibling_completed = False

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        nonlocal handled
        handled += 1
        worker.request_stop()
        finished.set()

    async def sibling() -> None:
        nonlocal sibling_completed
        await finished.wait()
        sibling_completed = True

    worker = TorrentWorker(
        outage_sessions,
        RedisCoordinator.unconfigured(),
        {"OUTAGE_TEST": handler},
        worker_id="outage-worker",
        config=CONFIG,
        clock=lambda: NOW,
    )
    async with asyncio.timeout(2), asyncio.TaskGroup() as tasks:
        tasks.create_task(worker.run())
        tasks.create_task(sibling())
    assert sibling_completed and handled == 1
    assert outage_sessions.calls[2] - outage_sessions.calls[1] >= 0.025
    async with outage_sessions.healthy() as session:
        completed = await session.get(TorrentJob, job.id)
        assert completed is not None and completed.state is TorrentJobState.COMPLETED
        assert completed.attempt_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "retention"])
async def test_periodic_worker_job_retries_raw_connection_failure(
    outage_sessions: OutageSessions,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.jobs import torrent_effects

    monkeypatch.setattr(torrent_effects, "DATABASE_RETRY_SECONDS", 0.03)
    outage_sessions.failures_left = 1
    redis = RedisCoordinator.unconfigured()
    if kind == "sync":
        sync = TorrentSyncEnqueuer(outage_sessions, redis, clock=lambda: NOW)
        original_enqueue = sync.enqueue_once

        async def enqueue() -> tuple[int, int]:
            result = await original_enqueue()
            sync.request_stop()
            return result

        monkeypatch.setattr(sync, "enqueue_once", enqueue)
        await asyncio.wait_for(sync.run(), 2)
    else:
        reaper = TorrentRetentionReaper(outage_sessions, redis, clock=lambda: NOW)
        original_expire = reaper.expire_once

        async def expire() -> int:
            result = await original_expire()
            reaper.request_stop()
            return result

        monkeypatch.setattr(reaper, "expire_once", expire)
        await asyncio.wait_for(reaper.run(), 2)
    assert len(outage_sessions.calls) == 2
    assert outage_sessions.calls[1] - outage_sessions.calls[0] >= 0.025
    assert "database_unavailable" in caplog.text
    assert "FAKE_PRIVATE_PASSWORD" not in caplog.text


@pytest.mark.asyncio
async def test_failed_job_finalization_leaves_claim_for_durable_recovery(
    outage_sessions: OutageSessions,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = await add_job(outage_sessions)
    clock = [NOW]
    handled = 0

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        nonlocal handled
        handled += 1
        if handled == 1:
            outage_sessions.failures_left = 1

    worker = TorrentWorker(
        outage_sessions,
        RedisCoordinator.unconfigured(),
        {"OUTAGE_TEST": handler},
        worker_id="outage-worker",
        config=CONFIG,
        clock=lambda: clock[0],
    )
    assert await worker.process_once()
    async with outage_sessions.healthy() as session:
        abandoned = await session.get(TorrentJob, job.id)
        assert abandoned is not None and abandoned.state is TorrentJobState.RUNNING
        assert abandoned.attempt_count == 1
    clock[0] += CONFIG.claim_ttl
    assert await worker.process_once() is False  # Recovery sets the normal retry delay.
    clock[0] += CONFIG.retry_base
    assert await worker.process_once()
    async with outage_sessions.healthy() as session:
        completed = await session.get(TorrentJob, job.id)
        assert completed is not None and completed.state is TorrentJobState.COMPLETED
        assert completed.attempt_count == 2
    assert handled == 2
    assert "torrent_worker_database_unavailable" in caplog.text
    assert "FAKE_PRIVATE_PASSWORD" not in caplog.text


@pytest.mark.asyncio
async def test_raw_heartbeat_failure_cancels_handler_without_releasing_claim(
    outage_sessions: OutageSessions,
    caplog: pytest.LogCaptureFixture,
) -> None:
    job = await add_job(outage_sessions)
    cancelled = asyncio.Event()

    async def handler(_snapshot: TorrentJobSnapshot) -> None:
        outage_sessions.failures_left = 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    worker = TorrentWorker(
        outage_sessions,
        RedisCoordinator.unconfigured(),
        {"OUTAGE_TEST": handler},
        worker_id="outage-worker",
        config=CONFIG,
        clock=lambda: NOW,
    )
    assert await asyncio.wait_for(worker.process_once(), 2)
    assert cancelled.is_set()
    async with outage_sessions.healthy() as session:
        abandoned = await session.get(TorrentJob, job.id)
        assert abandoned is not None and abandoned.state is TorrentJobState.RUNNING
        assert abandoned.attempt_count == 1
    assert any(
        getattr(record, "cause_type", None) == "TorrentJobClaimLostError"
        for record in caplog.records
    )
    assert "FAKE_PRIVATE_PASSWORD" not in caplog.text


@pytest.mark.asyncio
async def test_worker_startup_retries_database_connection_then_loads_real_options(
    outage_sessions: OutageSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import worker as entrypoint

    monkeypatch.setattr(entrypoint, "session_factory", outage_sessions)
    monkeypatch.setattr(entrypoint, "WORKER_DATABASE_RETRY_SECONDS", 0.03)
    outage_sessions.failures_left = 2
    config = await asyncio.wait_for(entrypoint._load_worker_config(asyncio.Event()), 2)
    assert config is not None and 1 <= config.concurrency <= 16
    assert len(outage_sessions.calls) == 3
    assert outage_sessions.calls[1] - outage_sessions.calls[0] >= 0.025


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["connection", "retry_wait"])
async def test_stop_interrupts_worker_startup_database_work(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    from app import worker as entrypoint

    stop, entered = asyncio.Event(), asyncio.Event()
    reading_cancelled = False
    attempts = 0

    async def read() -> TorrentWorkerConfig:
        nonlocal reading_cancelled, attempts
        attempts += 1
        entered.set()
        if phase == "retry_wait":
            raise OSError("FAKE_PRIVATE_CONNECTION_ERROR")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            reading_cancelled = True
            raise
        pytest.fail("Connection work must have been cancelled")

    monkeypatch.setattr(entrypoint, "_read_worker_config", read)
    task = asyncio.create_task(entrypoint._load_worker_config(stop))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.sleep(0.01)  # Let the completed failure enter its five-second wait.
        stop.set()
        assert await asyncio.wait_for(task, 1) is None
        assert attempts == 1
        assert reading_cancelled == (phase == "connection")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_invalid_worker_configuration_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import worker as entrypoint

    async def read() -> TorrentWorkerConfig:
        raise RuntimeError("worker_concurrency_option_invalid")

    monkeypatch.setattr(entrypoint, "_read_worker_config", read)
    with pytest.raises(RuntimeError, match="worker_concurrency_option_invalid"):
        await entrypoint._load_worker_config(asyncio.Event())


@pytest.mark.asyncio
async def test_retention_success_keeps_hourly_cadence_and_stops_promptly(
    outage_sessions: OutageSessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaper = TorrentRetentionReaper(
        outage_sessions, RedisCoordinator.unconfigured(), clock=lambda: NOW
    )
    finished = asyncio.Event()
    original = reaper.expire_once

    async def expire() -> int:
        result = await original()
        finished.set()
        return result

    monkeypatch.setattr(reaper, "expire_once", expire)
    task = asyncio.create_task(reaper.run())
    try:
        await asyncio.wait_for(finished.wait(), 1)
        await asyncio.sleep(0.08)
        assert len(outage_sessions.calls) == 1
        reaper.request_stop()
        await asyncio.wait_for(task, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "retention"])
async def test_unexpected_periodic_worker_error_is_not_hidden(
    outage_sessions: OutageSessions,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    async def unexpected() -> None:
        raise RuntimeError("unexpected_test_error")

    if kind == "sync":
        sync = TorrentSyncEnqueuer(outage_sessions, RedisCoordinator.unconfigured())
        monkeypatch.setattr(sync, "enqueue_once", unexpected)
        with pytest.raises(RuntimeError, match="unexpected_test_error"):
            await sync.run()
    else:
        reaper = TorrentRetentionReaper(outage_sessions, RedisCoordinator.unconfigured())
        monkeypatch.setattr(reaper, "expire_once", unexpected)
        with pytest.raises(RuntimeError, match="unexpected_test_error"):
            await reaper.run()


@pytest.mark.asyncio
async def test_startup_cancellation_cleans_up_connection_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import worker as entrypoint

    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def read() -> TorrentWorkerConfig:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        pytest.fail("Connection work must be cancelled")

    monkeypatch.setattr(entrypoint, "_read_worker_config", read)
    task = asyncio.create_task(entrypoint._load_worker_config(asyncio.Event()))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
