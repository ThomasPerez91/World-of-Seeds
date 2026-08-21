from __future__ import annotations

import os
import uuid
from collections.abc import Callable

import pytest
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError

from app.coordination.redis import CacheState, JsonValue, RedisCoordinator
from app.core.config import Settings


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}
        self.fail = False
        self.closed = False

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

    async def aclose(self) -> None:
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
    assert (await unconfigured.check_health()).state == "unconfigured"


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
    finally:
        await redis.aclose()
