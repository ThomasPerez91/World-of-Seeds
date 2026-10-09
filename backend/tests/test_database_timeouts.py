import asyncio
import os
from time import monotonic
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.coordination import RedisCoordinator
from app.core.config import Settings
from app.core.database import create_database_engine
from app.jobs.worker import TorrentJobSnapshot, TorrentWorker


@pytest.mark.parametrize(
    "field",
    [
        "database_connect_timeout_seconds",
        "database_command_timeout_seconds",
        "database_pool_timeout_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1, 31, float("inf"), float("nan")])
def test_database_deadlines_cannot_be_disabled(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


@pytest.mark.asyncio
async def test_sqlite_sessions_keep_working_without_postgres_arguments() -> None:
    engine = create_database_engine(Settings(database_url="sqlite+aiosqlite://"))
    try:
        async with engine.begin() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_runtime_deadlines_reach_the_driver_and_pool() -> None:
    engine = create_database_engine(
        Settings(
            database_url="postgresql+asyncpg://fake:fake@127.0.0.1/fake",
            database_connect_timeout_seconds=0.2,
            database_command_timeout_seconds=0.4,
            database_pool_timeout_seconds=0.3,
        )
    )
    received: dict[str, object] = {}

    class CapturedConnect(Exception):
        pass

    @event.listens_for(engine.sync_engine, "do_connect")
    def capture_connect(
        dialect: object, record: object, args: object, kwargs: dict[str, object]
    ) -> None:
        received.update(kwargs)
        raise CapturedConnect

    try:
        with pytest.raises(CapturedConnect):
            async with engine.connect():
                pass
        assert received["timeout"] == 0.2
        assert received["command_timeout"] == 0.4
        assert engine.pool.timeout() == 0.3  # type: ignore[attr-defined]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["connection", "worker_claim", "worker_recovery"])
async def test_silent_postgres_handshake_times_out_and_can_retry(
    consumer: str, caplog: pytest.LogCaptureFixture
) -> None:
    writers: list[asyncio.StreamWriter] = []
    accepted = asyncio.Event()

    def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writers.append(writer)
        accepted.set()
        # Accept TCP but deliberately never answer the PostgreSQL handshake.

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    engine = create_database_engine(
        Settings(
            database_url=f"postgresql+asyncpg://fake:fake@127.0.0.1:{port}/fake",
            database_connect_timeout_seconds=0.1,
        )
    )

    async def unused(_job: TorrentJobSnapshot) -> None:
        pytest.fail("An unavailable database must not start a handler")

    worker = TorrentWorker(
        async_sessionmaker(engine),
        RedisCoordinator.unconfigured(),
        {"SILENT_TEST": unused},
        worker_id="silent-test",
    )
    try:
        for _ in range(2):
            started = monotonic()
            # The outer watchdog must not be the source of the expected timeout.
            async with asyncio.timeout(2):
                if consumer == "connection":
                    with pytest.raises(TimeoutError):
                        async with engine.connect():
                            pytest.fail("A silent server cannot supply a SQL connection")
                elif consumer == "worker_claim":
                    assert await worker._claim_one() is None
                else:
                    await worker._recover_if_due(force=True)
                    assert worker._last_recovery is None
            assert 0.05 <= monotonic() - started < 1.5
        assert accepted.is_set()
        assert len(writers) == 2
        if consumer != "connection":
            assert "torrent_worker_database_unavailable" in caplog.text
            assert "fake:fake" not in caplog.text
    finally:
        server.close()
        for writer in writers:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for writer in writers), return_exceptions=True)
        await server.wait_closed()
        await engine.dispose()


POSTGRES = pytest.mark.skipif(
    not os.environ.get("WOS_DATABASE_URL", "").startswith("postgresql+asyncpg"),
    reason="Requires the isolated PostgreSQL CI service",
)


@POSTGRES
@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["slow_query", "lock_wait"])
async def test_postgres_command_deadline_rolls_back_and_pool_recovers(operation: str) -> None:
    engine = create_database_engine(
        Settings(database_command_timeout_seconds=0.2, database_connect_timeout_seconds=2)
    )
    key = uuid4().int % (2**63 - 1)
    try:
        async with engine.connect() as blocker:
            await blocker.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
            async with engine.connect() as connection:
                started = monotonic()
                async with asyncio.timeout(3):
                    with pytest.raises(TimeoutError):
                        if operation == "slow_query":
                            await connection.execute(text("SELECT pg_sleep(5)"))
                        else:
                            await connection.execute(
                                text("SELECT pg_advisory_xact_lock(:key)"), {"key": key}
                            )
                assert 0.1 <= monotonic() - started < 2
                await connection.rollback()
                assert await connection.scalar(text("SELECT 1")) == 1
        # Checkout performs pre-ping too; an aborted command must not poison the pool.
        async with engine.connect() as recovered:
            assert await recovered.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


@POSTGRES
@pytest.mark.asyncio
async def test_postgres_pool_exhaustion_has_a_deadline_and_recovers() -> None:
    engine = create_database_engine(Settings(database_pool_timeout_seconds=0.1))
    connections = []
    try:
        # Keep the existing default capacity: five pooled plus ten overflow connections.
        connections = await asyncio.gather(*(engine.connect() for _ in range(15)))
        started = monotonic()
        async with asyncio.timeout(3):
            with pytest.raises(PoolTimeoutError):
                async with engine.connect():
                    pytest.fail("An exhausted pool must not admit another connection")
        assert 0.05 <= monotonic() - started < 2
        await connections.pop().close()
        async with engine.connect() as recovered:
            assert await recovered.scalar(text("SELECT 1")) == 1
    finally:
        await asyncio.gather(*(connection.close() for connection in connections))
        await engine.dispose()
