"""Per-account budget of business requests for the deliberately single-process API.

Every authenticated HTTP request consumes one unit of its account's budget, read from the
``WOS_REQUEST_RATE_LIMIT_PER_MINUTE`` option. File transfers are exempt: their routes are
marked with :func:`business_rate_limit_exempt` and keep their own lease and lane controls, so a
folder downloaded file by file is never throttled here.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from time import monotonic

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DatabaseOption
from app.options.registry import DATABASE_OPTION_SPECS

OPTION_KEY = "WOS_REQUEST_RATE_LIMIT_PER_MINUTE"
_EXEMPT_ATTRIBUTE = "__wos_business_rate_limit_exempt__"
_SPEC = next(spec for spec in DATABASE_OPTION_SPECS if spec.key == OPTION_KEY)
DEFAULT_MAXIMUM = int(_SPEC.default)


def business_rate_limit_exempt[Endpoint: Callable[..., object]](endpoint: Endpoint) -> Endpoint:
    """Mark a route whose requests are file transfers rather than business requests."""
    setattr(endpoint, _EXEMPT_ATTRIBUTE, True)
    return endpoint


def is_business_rate_limit_exempt(endpoint: object) -> bool:
    return getattr(endpoint, _EXEMPT_ATTRIBUTE, False) is True


@dataclass(slots=True)
class _Budget:
    window_start: float
    current: int = 0
    previous: int = 0


class AccountRequestRateLimiter:
    """Sliding-window estimate per account, in constant memory per account.

    The estimate weighs the previous fixed window by the share of it that still overlaps the
    rolling minute, which avoids the double burst of a plain fixed window. Accounts are created
    by administrators, so their number is small; past ``max_accounts`` the least recently seen
    budget is dropped, which can only be generous to an idle account.
    """

    def __init__(
        self,
        *,
        window_seconds: int = 60,
        max_accounts: int = 10_000,
        option_ttl_seconds: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if min(window_seconds, max_accounts) < 1 or option_ttl_seconds < 0:
            raise ValueError("invalid account request limits")
        self.window_seconds = window_seconds
        self.max_accounts = max_accounts
        self.option_ttl_seconds = option_ttl_seconds
        self._clock = clock
        self._budgets: OrderedDict[uuid.UUID, _Budget] = OrderedDict()
        self._maximum: int | None = None
        self._maximum_read_at = 0.0
        self._lock = asyncio.Lock()

    async def maximum(self, session: AsyncSession) -> int:
        """The configured budget, read from PostgreSQL at most once per TTL."""
        now = self._clock()
        if self._maximum is not None and now - self._maximum_read_at < self.option_ttl_seconds:
            return self._maximum
        value = await session.scalar(
            select(DatabaseOption.integer_value).where(DatabaseOption.key == OPTION_KEY)
        )
        # Bounds are enforced by the option registry; a missing row means a fresh database
        # whose bootstrap has not run yet.
        self._maximum = value if isinstance(value, int) and value > 0 else DEFAULT_MAXIMUM
        self._maximum_read_at = now
        return self._maximum

    async def consume(self, account: uuid.UUID, maximum: int) -> int | None:
        """Charge one request; return the seconds to wait when the budget is exhausted."""
        async with self._lock:
            now = self._clock()
            budget = self._budgets.get(account)
            if budget is None:
                while len(self._budgets) >= self.max_accounts:
                    self._budgets.popitem(last=False)
                budget = _Budget(window_start=now)
                self._budgets[account] = budget
            self._budgets.move_to_end(account)

            elapsed_windows = int((now - budget.window_start) // self.window_seconds)
            if elapsed_windows >= 1:
                budget.previous = budget.current if elapsed_windows == 1 else 0
                budget.current = 0
                budget.window_start += elapsed_windows * self.window_seconds

            into_window = now - budget.window_start
            overlap = 1.0 - into_window / self.window_seconds
            estimate = budget.previous * overlap + budget.current
            if estimate + 1 > maximum:
                return self._retry_after(budget, into_window, maximum)
            budget.current += 1
            return None

    def _retry_after(self, budget: _Budget, into_window: float, maximum: int) -> int:
        remaining = self.window_seconds - into_window
        if budget.current >= maximum or budget.previous == 0:
            # Only the next window frees this budget.
            return max(1, ceil(remaining))
        # The previous window's weight decays linearly: wait until one unit has drained.
        needed = budget.previous + budget.current + 1 - maximum
        seconds = needed * self.window_seconds / budget.previous
        return max(1, min(ceil(remaining), ceil(seconds - into_window)))
