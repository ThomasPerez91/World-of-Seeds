from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.routes.torrents_v2 import stream_torrent_events
from app.auth.dependencies import RealtimeAuthContext
from app.coordination import TorrentEventType, TorrentRealtimeEvent


class OneEventSubscription:
    def __init__(self, event: TorrentRealtimeEvent) -> None:
        self.event = event
        self.delivered = False
        self.closed = False

    async def next_event(self, *, timeout_seconds: float) -> TorrentRealtimeEvent | None:
        assert timeout_seconds > 0
        if self.delivered:
            raise WebSocketDisconnect(code=1000)
        self.delivered = True
        return self.event

    async def aclose(self) -> None:
        self.closed = True


class RealtimeRedis:
    def __init__(self, event: TorrentRealtimeEvent, *, available: bool = True) -> None:
        self.event = event
        self.available = available
        self.user_ids: list[uuid.UUID] = []
        self.subscriptions: list[OneEventSubscription] = []

    async def subscribe_torrent_events(self, user_id: uuid.UUID) -> OneEventSubscription | None:
        self.user_ids.append(user_id)
        if not self.available:
            return None
        subscription = OneEventSubscription(self.event)
        self.subscriptions.append(subscription)
        return subscription


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.messages: list[dict[str, str]] = []
        self.close_code: int | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, str]) -> None:
        self.messages.append(message)

    async def close(self, *, code: int) -> None:
        self.close_code = code


@pytest.mark.asyncio
@pytest.mark.parametrize("connection_count", [10, 25, 50, 100])
async def test_realtime_endpoint_scales_without_database_sessions(
    connection_count: int,
) -> None:
    user_id = uuid.uuid4()
    event = TorrentRealtimeEvent(TorrentEventType.READY, uuid.uuid4(), datetime.now(UTC))
    redis = RealtimeRedis(event)
    sockets = [FakeWebSocket() for _ in range(connection_count)]

    await asyncio.gather(
        *(
            stream_torrent_events(
                cast(Any, socket),
                cast(Any, redis),
                RealtimeAuthContext(user_id),
            )
            for socket in sockets
        )
    )

    assert redis.user_ids == [user_id] * connection_count
    assert all(socket.accepted for socket in sockets)
    assert all(socket.messages == [event.payload()] for socket in sockets)
    assert all(subscription.closed for subscription in redis.subscriptions)


@pytest.mark.asyncio
async def test_realtime_endpoint_requests_authoritative_resync_when_redis_is_down() -> None:
    user_id = uuid.uuid4()
    event = TorrentRealtimeEvent(TorrentEventType.READY, uuid.uuid4(), datetime.now(UTC))
    redis = RealtimeRedis(event, available=False)
    socket = FakeWebSocket()

    await stream_torrent_events(
        cast(Any, socket),
        cast(Any, redis),
        RealtimeAuthContext(user_id),
    )

    assert socket.accepted is True
    assert socket.messages == [{"type": "resync_required"}]
    assert socket.close_code == 1013
