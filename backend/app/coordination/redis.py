from __future__ import annotations

import json
import math
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.coordination.events import TorrentEventType, TorrentRealtimeEvent
from app.core.config import Settings

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
type RedisState = Literal["healthy", "unavailable", "unconfigured"]

_KEY_PART = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class RedisCommands(Protocol):
    async def ping(self) -> bool: ...

    async def get(self, name: str) -> str | bytes | None: ...

    async def set(self, name: str, value: str, *, ex: int) -> object: ...

    async def delete(self, *names: str) -> int: ...

    async def lpush(self, name: str, *values: str) -> int: ...

    async def ltrim(self, name: str, start: int, end: int) -> object: ...

    async def blpop(self, keys: str, blocking_seconds: int) -> tuple[str, str] | None: ...

    async def publish(self, channel: str, message: str) -> int: ...

    def pubsub(self) -> RedisPubSub: ...

    async def aclose(self) -> None: ...


class RedisPubSub(Protocol):
    async def subscribe(self, *channels: str) -> None: ...

    async def get_message(
        self,
        ignore_subscribe_messages: bool,
        wait_seconds: float,
    ) -> dict[str, object] | None: ...

    async def unsubscribe(self, *channels: str) -> None: ...

    async def aclose(self) -> None: ...


class RedisSubscriptionUnavailable(RuntimeError):
    """A best-effort realtime subscription cannot currently continue."""


class TorrentEventSubscription:
    def __init__(self, pubsub: RedisPubSub, channels: tuple[str, ...]) -> None:
        self._pubsub = pubsub
        self._channels = channels
        self._closed = False

    async def next_event(self, *, timeout_seconds: float) -> TorrentRealtimeEvent | None:
        if not 0 < timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 0 and 300")
        if self._closed:
            raise RedisSubscriptionUnavailable("torrent event subscription is closed")
        try:
            message = await self._pubsub.get_message(True, timeout_seconds)
        except (RedisError, OSError, TimeoutError) as exc:
            raise RedisSubscriptionUnavailable("torrent event subscription failed") from exc
        if message is None:
            return None
        raw = message.get("data")
        if not isinstance(raw, str | bytes):
            return None
        try:
            return TorrentRealtimeEvent.decode(raw)
        except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
            return None

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._pubsub.unsubscribe(*self._channels)
            await self._pubsub.aclose()
        except (RedisError, OSError, TimeoutError):
            return


class CacheState(StrEnum):
    MISSING = "MISSING"
    FRESH = "FRESH"
    STALE = "STALE"


@dataclass(frozen=True)
class CacheLookup:
    state: CacheState
    value: JsonValue = None


@dataclass(frozen=True)
class RedisHealth:
    state: RedisState
    checked_at: datetime
    latency_ms: int | None = None
    error_code: str | None = None

    @property
    def permits_requests(self) -> bool:
        return self.state != "unavailable"


class RedisCoordinator:
    """Best-effort Redis acceleration with no durable business authority."""

    def __init__(
        self,
        client: RedisCommands | None,
        *,
        namespace: str,
        cache_ttl_seconds: int,
        cache_stale_seconds: int,
        signal_queue_max_length: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._namespace = namespace
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_stale_seconds = cache_stale_seconds
        self._signal_queue_max_length = signal_queue_max_length
        self._clock = clock

    @classmethod
    def from_settings(cls, settings: Settings) -> RedisCoordinator:
        client: RedisCommands | None = None
        if settings.redis_url is not None:
            client = cast(
                RedisCommands,
                Redis.from_url(
                    settings.redis_url.get_secret_value(),
                    decode_responses=True,
                    socket_connect_timeout=settings.redis_connect_timeout_seconds,
                    socket_timeout=settings.redis_socket_timeout_seconds,
                    health_check_interval=30,
                ),
            )
        return cls(
            client,
            namespace=settings.redis_namespace,
            cache_ttl_seconds=settings.redis_cache_ttl_seconds,
            cache_stale_seconds=settings.redis_cache_stale_seconds,
            signal_queue_max_length=settings.redis_signal_queue_max_length,
        )

    @classmethod
    def unconfigured(cls) -> RedisCoordinator:
        return cls(
            None,
            namespace="wos:v2",
            cache_ttl_seconds=60,
            cache_stale_seconds=300,
            signal_queue_max_length=1_000,
        )

    @property
    def configured(self) -> bool:
        return self._client is not None

    def _key(self, category: str, namespace: str, key: str) -> str:
        for part in (category, namespace, key):
            if _KEY_PART.fullmatch(part) is None:
                raise ValueError("Redis key parts must be safe opaque identifiers")
        return f"{self._namespace}:{category}:{namespace}:{key}"

    @property
    def _signal_key(self) -> str:
        return f"{self._namespace}:signals:torrent-jobs"

    def _torrent_event_channel(self, user_id: uuid.UUID) -> str:
        return self._key("events", "torrent", user_id.hex)

    @property
    def _torrent_queue_event_channel(self) -> str:
        return f"{self._namespace}:events:torrent-queue"

    async def check_health(self) -> RedisHealth:
        checked_at = datetime.now(UTC)
        if self._client is None:
            return RedisHealth(state="unconfigured", checked_at=checked_at)
        started_at = time.perf_counter()
        try:
            await self._client.ping()
        except (RedisError, OSError, TimeoutError):
            return RedisHealth(
                state="unavailable",
                checked_at=checked_at,
                error_code="redis_unavailable",
            )
        return RedisHealth(
            state="healthy",
            checked_at=checked_at,
            latency_ms=max(0, round((time.perf_counter() - started_at) * 1_000)),
        )

    async def signal_job_available(self) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.lpush(self._signal_key, "1")
            await self._client.ltrim(self._signal_key, 0, self._signal_queue_max_length - 1)
        except (RedisError, OSError, TimeoutError):
            return False
        return True

    async def wait_for_job_signal(self, *, timeout_seconds: float) -> bool:
        if not 0 < timeout_seconds <= 300:
            raise ValueError("timeout_seconds must be between 0 and 300")
        if self._client is None:
            return False
        try:
            signal = await self._client.blpop(
                self._signal_key,
                max(1, math.ceil(timeout_seconds)),
            )
        except (RedisError, OSError, TimeoutError):
            return False
        return signal is not None

    async def publish_torrent_event(
        self,
        user_id: uuid.UUID,
        event: TorrentRealtimeEvent,
    ) -> bool:
        if self._client is None:
            return False
        try:
            await self._client.publish(self._torrent_event_channel(user_id), event.encode())
        except (RedisError, OSError, TimeoutError):
            return False
        return True

    async def publish_torrent_queue_changed(self, occurred_at: datetime) -> bool:
        if self._client is None:
            return False
        event = TorrentRealtimeEvent(TorrentEventType.QUEUE_CHANGED, None, occurred_at)
        try:
            await self._client.publish(self._torrent_queue_event_channel, event.encode())
        except (RedisError, OSError, TimeoutError):
            return False
        return True

    async def subscribe_torrent_events(
        self,
        user_id: uuid.UUID,
    ) -> TorrentEventSubscription | None:
        if self._client is None:
            return None
        channels = (self._torrent_event_channel(user_id), self._torrent_queue_event_channel)
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(*channels)
            confirmed: set[str] = set()
            for _ in channels:
                confirmation = await pubsub.get_message(False, 1)
                if (
                    confirmation is None
                    or confirmation.get("type") != "subscribe"
                    or confirmation.get("channel") not in channels
                ):
                    raise RedisSubscriptionUnavailable("torrent subscription was not confirmed")
                confirmed.add(str(confirmation["channel"]))
        except (RedisError, OSError, TimeoutError):
            with suppress(RedisError, OSError, TimeoutError):
                await pubsub.aclose()
            return None
        except RedisSubscriptionUnavailable:
            with suppress(RedisError, OSError, TimeoutError):
                await pubsub.aclose()
            return None
        if confirmed != set(channels):
            with suppress(RedisError, OSError, TimeoutError):
                await pubsub.aclose()
            return None
        return TorrentEventSubscription(pubsub, channels)

    async def cache_lookup(self, namespace: str, key: str) -> CacheLookup:
        redis_key = self._key("cache", namespace, key)
        if self._client is None:
            return CacheLookup(CacheState.MISSING)
        try:
            raw = await self._client.get(redis_key)
        except (RedisError, OSError, TimeoutError):
            return CacheLookup(CacheState.MISSING)
        if raw is None:
            return CacheLookup(CacheState.MISSING)

        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            envelope = json.loads(raw)
            if (
                not isinstance(envelope, dict)
                or not isinstance(envelope.get("fresh_until"), int | float)
                or "value" not in envelope
            ):
                raise ValueError("invalid cache envelope")
            value = cast(JsonValue, envelope["value"])
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            await self.invalidate(namespace, key)
            return CacheLookup(CacheState.MISSING)

        state = CacheState.FRESH if self._clock() <= envelope["fresh_until"] else CacheState.STALE
        return CacheLookup(state, value)

    async def cache_store(self, namespace: str, key: str, value: JsonValue) -> bool:
        redis_key = self._key("cache", namespace, key)
        if self._client is None:
            return False
        envelope = json.dumps(
            {
                "fresh_until": self._clock() + self._cache_ttl_seconds,
                "value": value,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            await self._client.set(
                redis_key,
                envelope,
                ex=self._cache_ttl_seconds + self._cache_stale_seconds,
            )
        except (RedisError, OSError, TimeoutError):
            return False
        return True

    async def cache_aside(
        self,
        namespace: str,
        key: str,
        loader: Callable[[], Awaitable[JsonValue]],
    ) -> JsonValue:
        cached = await self.cache_lookup(namespace, key)
        if cached.state is CacheState.FRESH:
            return cached.value
        authoritative_value = await loader()
        await self.cache_store(namespace, key, authoritative_value)
        return authoritative_value

    async def invalidate(self, namespace: str, key: str) -> bool:
        redis_key = self._key("cache", namespace, key)
        if self._client is None:
            return False
        try:
            await self._client.delete(redis_key)
        except (RedisError, OSError, TimeoutError):
            return False
        return True

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except (RedisError, OSError, TimeoutError):
                return
