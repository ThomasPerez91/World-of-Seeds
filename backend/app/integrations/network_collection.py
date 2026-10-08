from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic

import httpx

from app.core.config import Settings
from app.integrations.http import integration_timeout
from app.integrations.prometheus_network import (
    NETWORK_FRESHNESS,
    NetworkPeriod,
    NetworkThroughputSnapshot,
    PrometheusNetworkClient,
    PrometheusNetworkError,
)

NETWORK_CACHE_SECONDS = 15.0
NETWORK_FAILURE_BACKOFF_SECONDS = 5.0


class NetworkThroughputCollector:
    """One bounded host-network snapshot per API process, never user data."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._snapshot: NetworkThroughputSnapshot | None = None
        self._expires_at = 0.0
        self._failed_until = 0.0

    async def snapshot(self, period: NetworkPeriod) -> NetworkThroughputSnapshot:
        if period != "realtime":
            raise ValueError("unsupported network period")
        async with self._lock:
            now = monotonic()
            if now < self._failed_until:
                raise PrometheusNetworkError("prometheus network query temporarily unavailable")
            if self._snapshot is not None and now < self._expires_at:
                return self._snapshot
            # Discard expired data before refreshing: failures cannot resurrect old rates.
            self._snapshot = None
            settings = self._settings
            if settings.prometheus_url is None:
                raise PrometheusNetworkError("prometheus network collection is unconfigured")
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=str(settings.prometheus_url).rstrip("/"),
                    timeout=integration_timeout(
                        settings.prometheus_connect_timeout_seconds,
                        settings.prometheus_read_timeout_seconds,
                    ),
                    trust_env=False,
                )
            try:
                snapshot = await PrometheusNetworkClient(
                    self._client, interface=settings.network_interface
                ).snapshot(period)
            except PrometheusNetworkError:
                self._failed_until = monotonic() + NETWORK_FAILURE_BACKOFF_SECONDS
                raise
            expires_at = now + NETWORK_CACHE_SECONDS
            # A cached sample must remain within the parser's original freshness budget.
            wall_now = datetime.now(UTC)
            for direction in (snapshot.download, snapshot.upload):
                if direction is not None and direction.samples:
                    remaining = (
                        direction.samples[-1].timestamp + NETWORK_FRESHNESS - wall_now
                    ).total_seconds()
                    expires_at = min(expires_at, monotonic() + max(0.0, remaining))
            self._snapshot = snapshot
            self._expires_at = expires_at
            return snapshot

    async def aclose(self) -> None:
        async with self._lock:
            if self._client is not None:
                await self._client.aclose()
                self._client = None
            self._snapshot = None
            self._expires_at = 0.0
            self._failed_until = 0.0
