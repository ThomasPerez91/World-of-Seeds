import asyncio
import os
import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.database import DatabaseTimeouts, create_database_engine, get_db_session
from app.main import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["internal", "external"])
@pytest.mark.parametrize("failure", ["connection", "pool", "timeout"])
async def test_real_database_outages_return_503_and_recover(
    tmp_path: Path, api: str, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    prefix = "/api/v2" if api == "internal" else "/api/external/v1"
    application = create_app(Settings(data_root=tmp_path, static_root=tmp_path / "absent"))
    closed = 0
    attempts = 0
    healthy = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.03,
    )
    reserved = socket.socket()
    reserved.bind(("127.0.0.1", 0))
    port = reserved.getsockname()[1]
    server = None
    writers: list[asyncio.StreamWriter] = []
    if failure == "timeout":
        reserved.close()

        def accept(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writers.append(writer)

        server = await asyncio.start_server(accept, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
    unavailable = create_database_engine(
        Settings(database_url=f"postgresql+asyncpg://fake:private-secret@127.0.0.1:{port}/fake"),
        timeouts=DatabaseTimeouts(connect_seconds=0.05),
    )
    active = healthy if failure == "pool" else unavailable
    occupied = await healthy.connect() if failure == "pool" else None

    async def sessions() -> AsyncIterator[AsyncSession]:
        nonlocal closed
        try:
            async with async_sessionmaker(active)() as session:
                yield session
        finally:
            closed += 1

    application.dependency_overrides[get_db_session] = sessions

    @application.get(prefix + "/availability-test")
    async def read_database(
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, int]:
        nonlocal attempts
        attempts += 1
        assert await session.scalar(text("SELECT 1")) == 1
        return {"result": 1}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=application, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            for _ in range(2):
                response = await client.get(
                    prefix + "/availability-test", headers={"X-Request-ID": "known-id"}
                )
                assert response.status_code == 503, response.text
                assert response.headers["retry-after"] == "5"
                assert response.headers["cache-control"] == "no-store"
                body = response.json()["detail" if api == "internal" else "error"]
                assert body["code"] == "database_unavailable"
                assert "private-secret" not in response.text and "SELECT" not in response.text
                if api == "external":
                    assert body["request_id"] == response.headers["x-request-id"] == "known-id"
            assert "api_database_unavailable" in caplog.text
            assert "private-secret" not in caplog.text and "SELECT" not in caplog.text
            assert attempts == closed == 2
            # No application replay: a fresh caller request resumes after pressure/outage ends.
            if occupied is not None:
                await occupied.close()
                occupied = None
            active = healthy
            recovered = await client.get(prefix + "/availability-test")
            assert recovered.status_code == 200
            assert attempts == closed == 3
            assert (await client.get("/api/v1/health/live")).status_code == 200
    finally:
        if occupied is not None:
            await occupied.close()
        reserved.close()
        if server is not None:
            server.close()
            for writer in writers:
                writer.close()
            await asyncio.gather(
                *(writer.wait_closed() for writer in writers), return_exceptions=True
            )
            await server.wait_closed()
        await unavailable.dispose()
        await healthy.dispose()


@pytest.mark.parametrize("state", ["08000", "08003", "08006", "57P01", "57P02", "57P03", "53300"])
def test_transient_postgres_states_are_classified(state: str) -> None:
    from sqlalchemy.exc import DBAPIError

    from app.core.database_errors import is_database_unavailable

    class DriverError(Exception):
        sqlstate = state

    assert is_database_unavailable(DriverError("PRIVATE"))
    assert is_database_unavailable(DBAPIError("PRIVATE SQL", {}, DriverError("PRIVATE")))


@pytest.mark.parametrize("state", ["23505", "23503", "42601", "42P01", "28P01"])
def test_constraint_programming_and_credentials_errors_are_not_unavailability(state: str) -> None:
    from sqlalchemy.exc import DBAPIError

    from app.core.database_errors import is_database_unavailable

    class DriverError(Exception):
        sqlstate = state

    assert not is_database_unavailable(DBAPIError("PRIVATE SQL", {}, DriverError("PRIVATE")))


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["internal", "external"])
async def test_unrelated_os_errors_keep_their_500_contract(tmp_path: Path, api: str) -> None:
    prefix = "/api/v2" if api == "internal" else "/api/external/v1"
    application = create_app(Settings(data_root=tmp_path, static_root=tmp_path / "absent"))

    @application.get(prefix + "/os-error-test")
    async def unrelated_error() -> None:
        raise OSError("PRIVATE filesystem operation")

    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.get(prefix + "/os-error-test")
    assert response.status_code == 500
    assert "retry-after" not in response.headers
    assert "database_unavailable" not in response.text
    assert "PRIVATE" not in response.text


@pytest.mark.asyncio
async def test_streaming_failure_never_sends_a_second_response() -> None:
    from starlette.types import Message, Receive, Scope, Send

    from app.api.external.middleware import ExternalRequestIdMiddleware
    from app.core.database_errors import DatabaseUnavailableError

    messages: list[Message] = []
    cleaned = False

    async def stream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal cleaned
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"partial", "more_body": True})
            raise DatabaseUnavailableError()
        finally:
            cleaned = True

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        messages.append(message)

    with pytest.raises(DatabaseUnavailableError):
        await ExternalRequestIdMiddleware(stream)(
            {"type": "http", "path": "/api/external/v1/stream", "headers": []}, receive, send
        )
    assert cleaned
    assert [
        message["status"] for message in messages if message["type"] == "http.response.start"
    ] == [200]
    assert messages[-1]["type"] == "http.response.body"
    assert messages[-1]["body"] == b"partial"


@pytest.mark.asyncio
async def test_failed_commit_is_rolled_back_without_application_replay(tmp_path: Path) -> None:
    from app.core.database_errors import DatabaseUnavailableError

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mutation.db'}")
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE markers (id INTEGER PRIMARY KEY)"))
    attempts = 0

    class UnavailableCommitSession(AsyncSession):
        async def commit(self) -> None:
            raise DatabaseUnavailableError()

    application = create_app(Settings(data_root=tmp_path, static_root=tmp_path / "absent"))

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with async_sessionmaker(engine, class_=UnavailableCommitSession)() as session:
            yield session

    application.dependency_overrides[get_db_session] = sessions

    @application.post("/api/v2/mutation-test")
    async def mutate(session: Annotated[AsyncSession, Depends(get_db_session)]) -> dict[str, bool]:
        nonlocal attempts
        attempts += 1
        await session.execute(text("INSERT INTO markers VALUES (1)"))
        await session.commit()
        return {"saved": True}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            response = await client.post("/api/v2/mutation-test")
        assert response.status_code == 503
        assert attempts == 1
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM markers")) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["login", "ready"])
async def test_actual_login_and_readiness_routes_fail_closed_during_connection_outage(
    tmp_path: Path, route: str
) -> None:
    application = create_app(Settings(data_root=tmp_path, static_root=tmp_path / "absent"))
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        engine = create_database_engine(
            Settings(database_url=f"postgresql+asyncpg://fake:private-secret@127.0.0.1:{port}/fake")
        )

        async def sessions() -> AsyncIterator[AsyncSession]:
            async with async_sessionmaker(engine)() as session:
                yield session

        application.dependency_overrides[get_db_session] = sessions
        try:
            async with AsyncClient(
                transport=ASGITransport(app=application), base_url="http://test"
            ) as client:
                response = (
                    await client.post(
                        "/api/v1/auth/login",
                        json={"username": "test-user", "password": "fake-password"},
                    )
                    if route == "login"
                    else await client.get("/api/v1/health/ready")
                )
                assert response.status_code == 503
                assert response.headers["retry-after"] == "5"
                assert response.headers["cache-control"] == "no-store"
                assert "set-cookie" not in response.headers
                assert "private-secret" not in response.text
                assert (await client.get("/api/v1/health/live")).status_code == 200
        finally:
            await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("WOS_DATABASE_URL", "").startswith("postgresql+asyncpg"),
    reason="Requires isolated PostgreSQL CI service",
)
@pytest.mark.parametrize("failure", ["pool", "disconnect"])
async def test_postgres_http_pressure_and_disconnect_recover(tmp_path: Path, failure: str) -> None:
    engine = create_database_engine(Settings(), timeouts=DatabaseTimeouts(pool_seconds=0.05))
    application = create_app(Settings(data_root=tmp_path, static_root=tmp_path / "absent"))
    occupied = []
    active = async_sessionmaker(engine)

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with active() as session:
            yield session

    application.dependency_overrides[get_db_session] = sessions

    @application.get("/api/v2/postgres-availability-test")
    async def read_database(
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, int]:
        assert await session.scalar(text("SELECT 1")) == 1
        return {"result": 1}

    try:
        if failure == "pool":
            occupied = await asyncio.gather(*(engine.connect() for _ in range(15)))
        else:
            connection = await engine.connect()
            occupied.append(connection)
            assert await connection.scalar(text("SELECT 1")) == 1
            await connection.rollback()
            raw = await connection.get_raw_connection()
            assert raw.driver_connection is not None
            raw.driver_connection.terminate()
            active = async_sessionmaker(connection)
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            async with asyncio.timeout(3):
                response = await client.get("/api/v2/postgres-availability-test")
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "database_unavailable"
            assert response.headers["retry-after"] == "5"
            await asyncio.gather(*(connection.close() for connection in occupied))
            occupied.clear()
            active = async_sessionmaker(engine)
            assert (await client.get("/api/v2/postgres-availability-test")).status_code == 200
    finally:
        await asyncio.gather(*(connection.close() for connection in occupied))
        await engine.dispose()
