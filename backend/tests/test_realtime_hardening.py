from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import WebSocketException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes import torrents_v2
from app.auth import dependencies
from app.auth.dependencies import (
    RealtimeAuthContext,
    RealtimeConnectionLimiter,
    get_realtime_connection,
    realtime_session_is_valid,
    websocket_origin_allowed,
)
from app.core.audit_log import log_admin_action
from app.models import Base, User, UserSession


def _websocket(**headers: str) -> Any:
    return SimpleNamespace(headers=headers)


def test_event_socket_is_refused_from_a_sibling_origin() -> None:
    same = _websocket(host="wos.example", origin="https://wos.example")
    same_with_port = _websocket(host="wos.example:8443", origin="https://wos.example:8443")
    sibling = _websocket(host="wos.example", origin="https://grafana.wos.example")
    foreign = _websocket(host="wos.example", origin="https://evil.test")
    opaque = _websocket(host="wos.example", origin="null")
    no_origin = _websocket(host="wos.example")

    assert websocket_origin_allowed(same) is True
    assert websocket_origin_allowed(same_with_port) is True
    assert websocket_origin_allowed(no_origin) is True
    assert websocket_origin_allowed(sibling) is False
    assert websocket_origin_allowed(foreign) is False
    assert websocket_origin_allowed(opaque) is False


def test_connection_limiter_bounds_each_user_and_the_total() -> None:
    limiter = RealtimeConnectionLimiter(per_user=2, total=3)
    first, second = uuid.uuid4(), uuid.uuid4()

    assert limiter.acquire(first) and limiter.acquire(first)
    assert limiter.acquire(first) is False
    assert limiter.acquire(second)
    assert limiter.acquire(uuid.uuid4()) is False  # total reached

    limiter.release(first)
    assert limiter.acquire(uuid.uuid4())
    for user in (first, second):
        limiter.release(user)
    limiter.release(uuid.uuid4())  # a spurious release never goes negative
    assert limiter.acquire(first)


@pytest.mark.asyncio
async def test_connection_dependency_holds_a_slot_for_the_socket_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RealtimeConnectionLimiter(per_user=1, total=10)
    monkeypatch.setattr(dependencies, "realtime_connections", limiter)
    context = RealtimeAuthContext(uuid.uuid4())

    first = get_realtime_connection(context)
    assert await anext(first) is context
    second = get_realtime_connection(context)
    with pytest.raises(WebSocketException) as refused:
        await anext(second)
    assert refused.value.code == 1013

    with pytest.raises(StopAsyncIteration):
        await anext(first)
    third = get_realtime_connection(context)
    assert await anext(third) is context
    await third.aclose()


@pytest_asyncio.fixture
async def sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'realtime.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dependencies, "session_factory", factory)
    yield factory
    await engine.dispose()


async def _session(
    factory: async_sessionmaker[AsyncSession],
    *,
    revoked: bool = False,
    expired: bool = False,
    active: bool = True,
) -> RealtimeAuthContext:
    now = datetime.now(UTC)
    async with factory() as db, db.begin():
        user = User(username=f"user-{uuid.uuid4().hex[:8]}", password_hash="hash", is_active=active)
        db.add(user)
        await db.flush()
        user_session = UserSession(
            user_id=user.id,
            token_hash=uuid.uuid4().hex.ljust(64, "0"),
            csrf_token_hash=uuid.uuid4().hex.ljust(64, "1"),
            expires_at=now - timedelta(minutes=1) if expired else now + timedelta(hours=1),
            revoked_at=now if revoked else None,
        )
        db.add(user_session)
        await db.flush()
        return RealtimeAuthContext(user.id, user_session.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "valid"),
    [
        ({}, True),
        ({"revoked": True}, False),
        ({"expired": True}, False),
        ({"active": False}, False),
    ],
)
async def test_open_socket_is_revalidated_against_its_session(
    sessions: async_sessionmaker[AsyncSession],
    options: dict[str, bool],
    valid: bool,
) -> None:
    context = await _session(sessions, **options)

    assert await realtime_session_is_valid(context) is valid


@pytest.mark.asyncio
async def test_a_context_without_a_session_is_not_revalidated() -> None:
    assert await realtime_session_is_valid(RealtimeAuthContext(uuid.uuid4())) is True


class _HeartbeatOnly:
    closed = False

    async def next_event(self, *, timeout_seconds: float) -> None:
        return None

    async def aclose(self) -> None:
        self.closed = True


class _Socket:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []
        self.close_code: int | None = None

    async def accept(self) -> None:
        return None

    async def send_json(self, message: dict[str, str]) -> None:
        self.messages.append(message)

    async def close(self, *, code: int) -> None:
        self.close_code = code


@pytest.mark.asyncio
async def test_a_revoked_session_ends_the_event_stream_at_the_next_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subscription = _HeartbeatOnly()

    class Redis:
        async def subscribe_torrent_events(self, user_id: uuid.UUID) -> _HeartbeatOnly:
            return subscription

    async def revoked(context: RealtimeAuthContext) -> bool:
        return False

    monkeypatch.setattr(torrents_v2, "realtime_session_is_valid", revoked)
    socket = _Socket()

    await torrents_v2.stream_torrent_events(
        cast(Any, socket),
        cast(Any, Redis()),
        RealtimeAuthContext(uuid.uuid4(), uuid.uuid4()),
    )

    assert socket.close_code == 1008
    assert socket.messages == []
    assert subscription.closed is True


def test_admin_actions_are_logged_with_visible_fields_and_no_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    actor = uuid.uuid4()
    with caplog.at_level(logging.INFO, logger="app.audit"):
        log_admin_action("cleanup_purge", actor, scheduled=3, skipped=1)
        log_admin_action("user_delete", actor)

    assert f"admin_action action=cleanup_purge actor={actor} scheduled=3 skipped=1" in caplog.text
    assert f"admin_action action=user_delete actor={actor}" in caplog.text


class _IdleSubscription:
    async def next_event(self, *, timeout_seconds: float) -> None:
        await asyncio.sleep(30)

    async def aclose(self) -> None:
        return None


class _IdleRedis:
    async def subscribe_torrent_events(self, user_id: uuid.UUID) -> _IdleSubscription:
        return _IdleSubscription()


def _refused_code(client: Any, url: str) -> int:
    from starlette.websockets import WebSocketDisconnect

    try:
        with client.websocket_connect(url):
            pass
    except WebSocketDisconnect as error:
        return int(error.code)
    raise AssertionError("the socket should have been refused")


def test_event_socket_slots_are_enforced_by_the_real_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.testclient import TestClient

    from app.auth.dependencies import get_realtime_auth_context
    from app.main import app

    user_id = uuid.uuid4()
    monkeypatch.setattr(
        dependencies, "realtime_connections", RealtimeConnectionLimiter(per_user=1, total=10)
    )
    app.dependency_overrides[get_realtime_auth_context] = lambda: RealtimeAuthContext(user_id)
    previous = getattr(app.state, "redis_coordinator", None)
    app.state.redis_coordinator = _IdleRedis()
    url = "ws://test/api/v2/torrents/events"
    try:
        client = TestClient(app, base_url="http://test")
        with client.websocket_connect(url):
            assert _refused_code(client, url) == 1013
        # The slot is released when the first socket ends.
        with client.websocket_connect(url):
            pass
    finally:
        app.dependency_overrides.pop(get_realtime_auth_context, None)
        app.state.redis_coordinator = previous
