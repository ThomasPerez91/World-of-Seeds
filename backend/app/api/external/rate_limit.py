from __future__ import annotations

import asyncio
import hashlib
import ipaddress
from collections import OrderedDict, deque
from collections.abc import Callable
from math import ceil
from time import monotonic


def _bucket_key(key: str) -> str:
    if not key.startswith("ip:"):
        return "client:" + hashlib.sha256(key.encode()).hexdigest()
    try:
        address = ipaddress.ip_address(key[3:].split("%", 1)[0])
    except ValueError:
        # Invalid/absent peer metadata must not create arbitrary fresh identities.
        identity = "unknown"
    else:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if isinstance(address, ipaddress.IPv6Address):
            identity = str(ipaddress.IPv6Network((address, 64), strict=False))
        else:
            identity = str(address)
    # Do not retain raw peer addresses in the process-local state.
    return "ip:" + hashlib.sha256(identity.encode()).hexdigest()


class ExternalApiRateLimiter:
    """Bounded rolling budgets for the deliberately single-process API.

    Each HTTP request consumes an IP gate before authentication; successful key
    lookup also consumes a client gate. Only the IP gate charges the global budget.
    No live budget is evicted to admit another identity.
    """

    def __init__(
        self,
        *,
        maximum: int = 60,
        window_seconds: int = 60,
        max_keys: int = 10_000,
        global_maximum: int = 6_000,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if min(maximum, window_seconds, max_keys, global_maximum) < 1:
            raise ValueError("invalid external API rate limits")
        self.maximum = maximum
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.global_maximum = global_maximum
        self._clock = clock
        # Ordered by last accepted event, not by last lookup/denial. Thus expiry
        # can remove inactive identities without scanning all live buckets.
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._global_events: deque[float] = deque()
        self._lock = asyncio.Lock()

    def _retry_after(self, expires_at: float, now: float) -> int:
        return max(1, ceil(expires_at - now))

    async def consume(self, key: str) -> int | None:
        async with self._lock:
            now = self._clock()
            cutoff = now - self.window_seconds
            while self._global_events and self._global_events[0] <= cutoff:
                self._global_events.popleft()
            while self._events:
                _, oldest = next(iter(self._events.items()))
                if oldest[-1] > cutoff:
                    break
                self._events.popitem(last=False)

            is_ip = key.startswith("ip:")
            if is_ip and len(self._global_events) >= self.global_maximum:
                return self._retry_after(self._global_events[0] + self.window_seconds, now)

            bucket = _bucket_key(key)
            events = self._events.get(bucket)
            if events is None:
                if len(self._events) >= self.max_keys:
                    oldest = next(iter(self._events.values()))
                    return self._retry_after(oldest[-1] + self.window_seconds, now)
                events = deque()
                self._events[bucket] = events
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.maximum:
                return self._retry_after(events[0] + self.window_seconds, now)
            events.append(now)
            self._events.move_to_end(bucket)
            if is_ip:
                self._global_events.append(now)
            return None
