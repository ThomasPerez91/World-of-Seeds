from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic


@dataclass(frozen=True, slots=True)
class OperationalMetricsSnapshot:
    """Bounded, secret-free database snapshot reused across Prometheus scrapes."""

    job_counts: tuple[tuple[str, int], ...]
    oldest_queued_at: datetime | None
    retries: int
    average_job_duration_seconds: float
    desired_generation: int
    applied_generation: int
    active_leases: int
    managed_bytes: int
    disk_total_bytes: int
    disk_free_bytes: int
    storage_pressure: str
    database_query_latency_seconds: float


type OperationalMetricsLoader = Callable[[], Awaitable[OperationalMetricsSnapshot]]


class OperationalMetricsCache:
    """Small reconstructible cache preventing a database audit on every scrape."""

    def __init__(self, *, ttl_seconds: float = 15.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("operational metrics cache TTL must be positive")
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()
        self._snapshot: OperationalMetricsSnapshot | None = None
        self._expires_at = 0.0

    async def get(self, loader: OperationalMetricsLoader) -> OperationalMetricsSnapshot:
        now = monotonic()
        if self._snapshot is not None and now < self._expires_at:
            return self._snapshot
        async with self._lock:
            now = monotonic()
            if self._snapshot is not None and now < self._expires_at:
                return self._snapshot
            snapshot = await loader()
            self._snapshot = snapshot
            self._expires_at = monotonic() + self._ttl_seconds
            return snapshot

    def invalidate(self) -> None:
        self._snapshot = None
        self._expires_at = 0.0
