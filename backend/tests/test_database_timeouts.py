import asyncio
import os
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.coordination import RedisCoordinator
from app.core.config import Settings
from app.core.database import DatabaseTimeouts, create_database_engine
from app.jobs.worker import TorrentJobSnapshot, TorrentWorker


@pytest.mark.parametrize(
    "field",
    [
        "connect_seconds",
        "command_seconds",
        "pool_seconds",
    ],
)
@pytest.mark.parametrize("value", [0, -1, 31, float("inf"), float("nan")])
def test_database_deadlines_cannot_be_disabled(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        DatabaseTimeouts(**{field: value})


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
        ),
        timeouts=DatabaseTimeouts(connect_seconds=0.2, command_seconds=0.4, pool_seconds=0.3),
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
        ),
        timeouts=DatabaseTimeouts(connect_seconds=0.1),
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
        Settings(), timeouts=DatabaseTimeouts(command_seconds=0.2, connect_seconds=2)
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
    engine = create_database_engine(Settings(), timeouts=DatabaseTimeouts(pool_seconds=0.1))
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


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_mode", ["deadline", "cancel"])
async def test_outer_deadline_terminates_before_waiting_for_cancellation(exit_mode: str) -> None:
    from app.core.database_driver import bounded_database_wait

    started = asyncio.Event()
    terminated = asyncio.Event()
    finished = asyncio.Event()

    async def stalled_operation() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Model cancellation that cannot finish without forcibly closing TCP.
            await terminated.wait()
        finally:
            finished.set()

    task = asyncio.create_task(
        bounded_database_wait(stalled_operation(), deadline_seconds=0.03, terminate=terminated.set)
    )
    await started.wait()
    if exit_mode == "cancel":
        task.cancel()
        expected: type[BaseException] = asyncio.CancelledError
    else:
        expected = TimeoutError
    async with asyncio.timeout(1):
        with pytest.raises(expected):
            await task
    assert terminated.is_set()
    assert finished.is_set()


@pytest.mark.asyncio
async def test_outer_deadline_preserves_success_and_unexpected_errors() -> None:
    from app.core.database_driver import bounded_database_wait

    def forbidden_termination() -> None:
        pytest.fail("A finished operation must not terminate the connection")

    async def result() -> int:
        return 7

    async def failure() -> int:
        raise RuntimeError("unexpected")

    assert (
        await bounded_database_wait(result(), deadline_seconds=0.1, terminate=forbidden_termination)
        == 7
    )
    with pytest.raises(RuntimeError, match="unexpected"):
        await bounded_database_wait(
            failure(), deadline_seconds=0.1, terminate=forbidden_termination
        )


@POSTGRES
@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["query", "pre_ping", "commit", "rollback"])
async def test_established_blackholed_connection_is_terminated_and_pool_recovers(
    phase: str,
) -> None:
    from sqlalchemy.engine import make_url

    target = make_url(Settings().sqlalchemy_database_url)
    blackhole = asyncio.Event()
    writers: list[asyncio.StreamWriter] = []
    handlers: set[asyncio.Task[None]] = set()

    async def proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        current = asyncio.current_task()
        assert current is not None
        handlers.add(current)
        writers.append(writer)
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                target.host, target.port or 5432
            )
            writers.append(upstream_writer)

            async def forward(
                source: asyncio.StreamReader, destination: asyncio.StreamWriter
            ) -> None:
                while data := await source.read(65536):
                    if not blackhole.is_set():
                        destination.write(data)
                        await destination.drain()

            async with asyncio.TaskGroup() as group:
                group.create_task(forward(reader, upstream_writer))
                group.create_task(forward(upstream_reader, writer))
        finally:
            handlers.discard(current)
            writer.close()

    server = await asyncio.start_server(proxy, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    url = target.set(host="127.0.0.1", port=port).render_as_string(hide_password=False)
    engine = create_database_engine(
        Settings(database_url=url), timeouts=DatabaseTimeouts(command_seconds=0.1)
    )
    try:
        if phase == "pre_ping":
            async with engine.connect() as primed:
                assert await primed.scalar(text("SELECT 1")) == 1
            blackhole.set()
            async with asyncio.timeout(4):
                with pytest.raises(TimeoutError):
                    async with engine.connect():
                        pytest.fail("A blackholed pre-ping must fail")
        else:
            async with engine.connect() as connection:
                assert await connection.scalar(text("SELECT 1")) == 1
                raw = await connection.get_raw_connection()
                driver = raw.driver_connection
                assert driver is not None
                blackhole.set()
                async with asyncio.timeout(4):
                    with pytest.raises(TimeoutError):
                        if phase == "query":
                            await connection.execute(text("SELECT 2"))
                        elif phase == "commit":
                            await connection.commit()
                        else:
                            await connection.rollback()
                assert driver.is_closed()
                await connection.invalidate()
        blackhole.clear()
        # Neither a terminated driver nor a stuck cancellation is returned to callers.
        async with asyncio.timeout(4), engine.connect() as recovered:
            assert await recovered.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()
        server.close()
        for writer in writers:
            writer.close()
        for handler in list(handlers):
            handler.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        await asyncio.gather(*(writer.wait_closed() for writer in writers), return_exceptions=True)
        await server.wait_closed()
