"""Pre-crypto IP budget for the deliberately single-process API."""

from collections import OrderedDict
from collections.abc import Callable
from math import ceil
from time import monotonic

from app.auth.security import hash_token


class LoginIpRateLimiter:
    def __init__(
        self,
        *,
        maximum: int = 30,
        window_seconds: int = 60,
        max_keys: int = 10_000,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if min(maximum, window_seconds, max_keys) < 1:
            raise ValueError("invalid login IP limits")
        self.maximum = maximum
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def consume(self, client_ip: str) -> int | None:
        # No await between read and update: concurrent requests on the API loop
        # cannot overspend the budget. Do not keep raw client addresses in memory.
        now = self._clock()
        while self._windows:
            _, (started, _) = next(iter(self._windows.items()))
            if started + self.window_seconds > now:
                break
            self._windows.popitem(last=False)
        key = hash_token(client_ip)
        entry = self._windows.get(key)
        if entry is None:
            if len(self._windows) >= self.max_keys:
                # Fail closed rather than evict live budgets or grow unboundedly.
                return self.window_seconds
            self._windows[key] = (now, 1)
            return None
        started, count = entry
        if count >= self.maximum:
            return max(1, ceil(started + self.window_seconds - now))
        self._windows[key] = (started, count + 1)
        return None
