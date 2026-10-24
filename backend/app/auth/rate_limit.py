"""Bounded pre-crypto budgets for the deliberately single-process API."""

import hashlib
import secrets
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from time import monotonic

from app.auth.security import hash_token, network_identity


@dataclass(slots=True)
class _Window:
    started: float
    count: int = 0


class LoginIpRateLimiter:
    def __init__(
        self,
        *,
        maximum: int = 30,
        window_seconds: int = 60,
        max_keys: int = 10_000,
        global_maximum: int = 6_000,
        fallback_maximum: int = 5,
        overflow_buckets: int = 4_096,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if (
            min(
                maximum,
                window_seconds,
                max_keys,
                global_maximum,
                fallback_maximum,
                overflow_buckets,
            )
            < 1
        ):
            raise ValueError("invalid login IP limits")
        self.maximum = maximum
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self.global_maximum = global_maximum
        self.fallback_maximum = fallback_maximum
        self._clock = clock
        self._windows: OrderedDict[str, _Window] = OrderedDict()
        # Fixed, secret-sharded overflow budgets: collisions only restrict access,
        # never reset a live budget or create new state. No raw IP/token/username.
        self._salt = secrets.token_bytes(32)
        self._overflow_ips: list[_Window | None] = [None] * overflow_buckets
        self._overflow_identities: list[_Window | None] = [None] * overflow_buckets
        self._global: _Window | None = None

    def _index(self, kind: str, identity: str) -> int:
        digest = hashlib.blake2b(
            (kind + "\0" + identity).encode(), key=self._salt, digest_size=8
        ).digest()
        return int.from_bytes(digest) % len(self._overflow_ips)

    def _live(self, window: _Window | None, now: float) -> _Window | None:
        return window if window is not None and window.started + self.window_seconds > now else None

    def _retry(self, window: _Window, now: float) -> int:
        return max(1, ceil(window.started + self.window_seconds - now))

    def consume(self, client_ip: str, *, identity: str | None = None) -> int | None:
        # No await: one API loop cannot overspend these budgets. Expiry order is
        # creation order; lookups and refusals never move or extend a window.
        now = self._clock()
        global_window = self._live(self._global, now)
        retry_after = (
            self._retry(global_window, now)
            if global_window is not None and global_window.count >= self.global_maximum
            else 0
        )
        while self._windows:
            _, oldest = next(iter(self._windows.items()))
            if self._live(oldest, now) is not None:
                break
            self._windows.popitem(last=False)

        network = network_identity(client_ip)
        key = hash_token(network)
        ip_index = self._index("ip", network)
        identity_index = self._index("identity", identity) if identity is not None else None
        account = (
            self._live(self._overflow_identities[identity_index], now)
            if identity_index is not None
            else None
        )
        window = self._windows.get(key)
        # Keep overflow budgets sticky until expiry, including after primary
        # capacity frees up. Promotion must not reset counters.
        overflow = self._live(self._overflow_ips[ip_index], now) if window is None else None
        use_overflow = window is None and (
            overflow is not None or len(self._windows) >= self.max_keys
        )
        if use_overflow:
            if identity_index is None:
                oldest = overflow or next(iter(self._windows.values()))
                return max(retry_after, self._retry(oldest, now))
            window = overflow or _Window(now)
            account = account or _Window(now)
        else:
            window = window or _Window(now)
        if window.count >= self.maximum:
            retry_after = max(retry_after, self._retry(window, now))
        if account is not None and account.count >= self.fallback_maximum:
            retry_after = max(retry_after, self._retry(account, now))
        if retry_after:
            return retry_after

        window.count += 1
        if use_overflow:
            self._overflow_ips[ip_index] = window
        else:
            self._windows[key] = window
        # A fallback identity remains protected when its source IP changes to an
        # admitted network; it cannot escape through an existing primary bucket.
        if account is not None and identity_index is not None:
            account.count += 1
            self._overflow_identities[identity_index] = account
        global_window = global_window or _Window(now)
        global_window.count += 1
        self._global = global_window
        return None
