from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError

from app.coordination import (
    RedisSubscriptionUnavailable,
    TorrentEventType,
    TorrentRealtimeEvent,
)
from app.coordination.redis import CacheState, JsonValue, RedisCoordinator
from app.core.config import Settings


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}
        self.fail = False
        self.closed = False
        self.subscribers: dict[str, list[FakePubSub]] = {}

    def _check(self) -> None:
        if self.fail:
            raise RedisConnectionError("test connection failure")

    async def ping(self) -> bool:
        self._check()
        return True

    async def get(self, name: str) -> str | None:
        self._check()
        return self.values.get(name)

    async def set(self, name: str, value: str, *, ex: int) -> bool:
        self._check()
        self.values[name] = value
        self.expirations[name] = ex
        return True

    async def delete(self, *names: str) -> int:
        self._check()
        deleted = 0
        for name in names:
            deleted += name in self.values
            self.values.pop(name, None)
        return deleted

    async def lpush(self, name: str, *values: str) -> int:
        self._check()
        target = self.lists.setdefault(name, [])
        for value in values:
            target.insert(0, value)
        return len(target)

    async def ltrim(self, name: str, start: int, end: int) -> bool:
        self._check()
        self.lists[name] = self.lists.get(name, [])[start : end + 1]
        return True

    async def blpop(self, keys: str, blocking_seconds: int) -> tuple[str, str] | None:
        self._check()
        values = self.lists.get(keys, [])
        if not values:
            return None
        return keys, values.pop()

    async def publish(self, channel: str, message: str) -> int:
        self._check()
        subscribers = tuple(self.subscribers.get(channel, ()))
        for subscriber in subscribers:
            subscriber.messages.put_nowait({"type": "message", "data": message})
        return len(subscribers)

    def pubsub(self) -> FakePubSub:
        return FakePubSub(self)

    async def aclose(self) -> None:
        self.closed = True


class FakePubSub:
    def __init__(self, client: FakeRedis) -> None:
        self.client = client
        self.channels: set[str] = set()
        self.messages: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.client._check()
        for channel in channels:
            self.channels.add(channel)
            self.client.subscribers.setdefault(channel, []).append(self)
            self.messages.put_nowait(
                {"type": "subscribe", "channel": channel, "data": len(self.channels)}
            )

    async def get_message(
        self,
        ignore_subscribe_messages: bool,
        wait_seconds: float,
    ) -> dict[str, object] | None:
        self.client._check()
        while True:
            try:
                message = await asyncio.wait_for(self.messages.get(), timeout=wait_seconds)
            except TimeoutError:
                return None
            if ignore_subscribe_messages and message.get("type") == "subscribe":
                continue
            return message

    async def unsubscribe(self, *channels: str) -> None:
        for channel in channels:
            subscribers = self.client.subscribers.get(channel, [])
            if self in subscribers:
                subscribers.remove(self)
            self.channels.discard(channel)

    async def aclose(self) -> None:
        await self.unsubscribe(*tuple(self.channels))
        self.closed = True


def coordinator(
    client: FakeRedis | None,
    *,
    clock: Callable[[], float] = lambda: 1_000.0,
) -> RedisCoordinator:
    return RedisCoordinator(
        client,
        namespace="wos:test",
        cache_ttl_seconds=60,
        cache_stale_seconds=300,
        signal_queue_max_length=2,
        clock=clock,
    )


@pytest.mark.asyncio
async def test_job_signal_is_namespaced_bounded_and_consumable() -> None:
    client = FakeRedis()
    redis = coordinator(client)

    assert await redis.signal_job_available() is True
    assert await redis.signal_job_available() is True
    assert await redis.signal_job_available() is True

    assert client.lists == {"wos:test:signals:torrent-jobs": ["1", "1"]}
    assert await redis.wait_for_job_signal(timeout_seconds=1) is True
    assert await redis.wait_for_job_signal(timeout_seconds=1) is True
    assert await redis.wait_for_job_signal(timeout_seconds=1) is False


@pytest.mark.asyncio
async def test_cache_aside_uses_fresh_value_and_rebuilds_stale_value() -> None:
    client = FakeRedis()
    now = [1_000.0]
    redis = coordinator(client, clock=lambda: now[0])
    loads = 0

    async def loader() -> JsonValue:
        nonlocal loads
        loads += 1
        return {"source": "postgres", "load": loads}

    first = await redis.cache_aside("torrents", "hash-1", loader)
    second = await redis.cache_aside("torrents", "hash-1", loader)
    assert first == second == {"source": "postgres", "load": 1}
    assert loads == 1
    assert client.expirations["wos:test:cache:torrents:hash-1"] == 360

    now[0] += 61
    stale = await redis.cache_lookup("torrents", "hash-1")
    rebuilt = await redis.cache_aside("torrents", "hash-1", loader)
    assert stale.state is CacheState.STALE
    assert rebuilt == {"source": "postgres", "load": 2}


@pytest.mark.asyncio
async def test_redis_loss_falls_back_to_authoritative_loader_without_raising() -> None:
    client = FakeRedis()
    client.fail = True
    redis = coordinator(client)
    loads = 0

    async def loader() -> JsonValue:
        nonlocal loads
        loads += 1
        return ["postgres-value"]

    value = await redis.cache_aside("requests", "request-1", loader)
    health = await redis.check_health()

    assert value == ["postgres-value"]
    assert loads == 1
    assert await redis.signal_job_available() is False
    assert health.state == "unavailable"
    assert health.error_code == "redis_unavailable"


@pytest.mark.asyncio
async def test_cache_loss_and_invalid_payload_are_reconstructed() -> None:
    client = FakeRedis()
    redis = coordinator(client)
    key = "wos:test:cache:torrents:hash-1"
    client.values[key] = "not-json"

    lookup = await redis.cache_lookup("torrents", "hash-1")
    assert lookup.state is CacheState.MISSING
    assert key not in client.values

    async def loader() -> JsonValue:
        return {"rebuilt": True}

    assert await redis.cache_aside("torrents", "hash-1", loader) == {"rebuilt": True}


@pytest.mark.asyncio
async def test_invalidation_and_unconfigured_mode_are_best_effort() -> None:
    client = FakeRedis()
    redis = coordinator(client)
    await redis.cache_store("requests", "request-1", {"state": "READY"})
    assert await redis.invalidate("requests", "request-1") is True

    unconfigured = RedisCoordinator.unconfigured()
    assert await unconfigured.signal_job_available() is False
    assert await unconfigured.publish_torrent_queue_changed(datetime.now(UTC)) is False
    assert (await unconfigured.check_health()).state == "unconfigured"
    assert await unconfigured.subscribe_torrent_events(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_torrent_events_are_secret_safe_namespaced_and_fan_out_to_every_tab() -> None:
    client = FakeRedis()
    redis = coordinator(client)
    user_id = uuid.uuid4()
    request_id = uuid.uuid4()
    first = await redis.subscribe_torrent_events(user_id)
    second = await redis.subscribe_torrent_events(user_id)
    other = await redis.subscribe_torrent_events(uuid.uuid4())
    assert first is not None and second is not None and other is not None
    event = TorrentRealtimeEvent(TorrentEventType.READY, request_id, datetime.now(UTC))

    assert await redis.publish_torrent_event(user_id, event) is True
    assert (await first.next_event(timeout_seconds=0.1)) == event
    assert (await second.next_event(timeout_seconds=0.1)) == event
    assert await other.next_event(timeout_seconds=0.01) is None
    payload = event.encode()
    assert set(event.payload()) == {"type", "request_id", "occurred_at"}
    assert "passkey" not in payload.lower()
    assert user_id.hex not in payload

    queue_changed_at = datetime.now(UTC)
    queue_event = TorrentRealtimeEvent(
        TorrentEventType.QUEUE_CHANGED,
        None,
        queue_changed_at,
    )
    assert await redis.publish_torrent_queue_changed(queue_changed_at) is True
    assert (await first.next_event(timeout_seconds=0.1)) == queue_event
    assert (await second.next_event(timeout_seconds=0.1)) == queue_event
    assert (await other.next_event(timeout_seconds=0.1)) == queue_event
    assert set(queue_event.payload()) == {"type", "occurred_at"}
    assert "request_id" not in queue_event.encode()
    assert TorrentRealtimeEvent.decode(queue_event.encode()) == queue_event
    await first.aclose()
    await second.aclose()
    await other.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("connection_count", [10, 25, 50, 100])
async def test_torrent_event_fanout_remains_bounded_at_expected_connection_counts(
    connection_count: int,
) -> None:
    client = FakeRedis()
    redis = coordinator(client)
    user_id = uuid.uuid4()
    subscriptions = [await redis.subscribe_torrent_events(user_id) for _ in range(connection_count)]
    assert all(subscription is not None for subscription in subscriptions)
    event = TorrentRealtimeEvent(TorrentEventType.STARTED, uuid.uuid4(), datetime.now(UTC))

    assert await redis.publish_torrent_event(user_id, event) is True
    received = await asyncio.gather(
        *(
            subscription.next_event(timeout_seconds=0.1)
            for subscription in subscriptions
            if subscription
        )
    )
    assert received == [event] * connection_count
    await asyncio.gather(*(subscription.aclose() for subscription in subscriptions if subscription))
    assert client.subscribers[f"wos:test:events:torrent:{user_id.hex}"] == []
    assert client.subscribers["wos:test:events:torrent-queue"] == []


@pytest.mark.asyncio
async def test_torrent_subscription_reports_redis_loss_without_durable_side_effect() -> None:
    client = FakeRedis()
    redis = coordinator(client)
    subscription = await redis.subscribe_torrent_events(uuid.uuid4())
    assert subscription is not None
    client.fail = True

    with pytest.raises(RedisSubscriptionUnavailable):
        await subscription.next_event(timeout_seconds=0.1)
    await subscription.aclose()


@pytest.mark.asyncio
async def test_real_redis_supports_signal_cache_and_reconstruction() -> None:
    redis_url = os.environ.get("WOS_REDIS_URL", "")
    if not redis_url:
        pytest.skip("real Redis test requires WOS_REDIS_URL")

    suffix = uuid.uuid4().hex
    settings = Settings(
        redis_url=SecretStr(redis_url),
        redis_namespace=f"wos:test:{suffix}",
    )
    redis = RedisCoordinator.from_settings(settings)
    try:
        assert (await redis.check_health()).state == "healthy"
        assert await redis.signal_job_available() is True
        assert await redis.wait_for_job_signal(timeout_seconds=1) is True

        async def loader() -> JsonValue:
            return {"source": "postgres"}

        assert await redis.cache_aside("managed", "torrent-1", loader) == {"source": "postgres"}
        assert (await redis.cache_lookup("managed", "torrent-1")).state is CacheState.FRESH
        assert await redis.invalidate("managed", "torrent-1") is True
        assert (await redis.cache_lookup("managed", "torrent-1")).state is CacheState.MISSING
        user_id = uuid.uuid4()
        subscription = await redis.subscribe_torrent_events(user_id)
        assert subscription is not None
        event = TorrentRealtimeEvent(TorrentEventType.READY, uuid.uuid4(), datetime.now(UTC))
        assert await redis.publish_torrent_event(user_id, event) is True
        assert await subscription.next_event(timeout_seconds=1) == event
        await subscription.aclose()
    finally:
        await redis.aclose()
