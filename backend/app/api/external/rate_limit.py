from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from time import monotonic


class ExternalApiRateLimiter:
    """Process-local limiter; WOS V2 deliberately runs one API process."""

    def __init__(self, *, maximum: int = 60, window_seconds: int = 60) -> None:
        self.maximum = maximum
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def consume(self, key: str) -> int | None:
        now = monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.maximum:
                return max(1, int(self.window_seconds - (now - events[0])) + 1)
            events.append(now)
            return None
