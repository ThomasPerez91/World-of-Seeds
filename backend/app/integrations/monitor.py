import asyncio
from datetime import UTC, datetime
from time import monotonic

import httpx

from app import __version__
from app.core.config import Settings
from app.integrations.http import integration_timeout
from app.integrations.newgreedy import NewGreedyClient
from app.integrations.qbittorrent import QBittorrentClient
from app.integrations.types import ExternalServicesSnapshot, ServiceProbe


class ExternalServicesMonitor:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._cached_snapshot: ExternalServicesSnapshot | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    async def snapshot(self, *, force: bool = False) -> ExternalServicesSnapshot:
        async with self._lock:
            now = monotonic()
            if (
                not force
                and self._cached_snapshot is not None
                and now - self._cached_at < self._settings.integration_health_cache_seconds
            ):
                return self._cached_snapshot

            newgreedy, qbittorrent = await asyncio.gather(
                self._probe_newgreedy(),
                self._probe_qbittorrent(),
            )
            result = ExternalServicesSnapshot(
                checked_at=datetime.now(UTC),
                newgreedy=newgreedy,
                qbittorrent=qbittorrent,
            )
            self._cached_snapshot = result
            self._cached_at = monotonic()
            return result

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=integration_timeout(
                self._settings.integration_connect_timeout_seconds,
                self._settings.integration_read_timeout_seconds,
            ),
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
            headers={"User-Agent": f"World-of-Seeds/{__version__}"},
        )

    async def _probe_newgreedy(self) -> ServiceProbe:
        if self._settings.newgreedy_url is None:
            return ServiceProbe(service="newgreedy", state="unconfigured")
        async with self._client() as client:
            return await NewGreedyClient(client, str(self._settings.newgreedy_url)).probe()

    async def _probe_qbittorrent(self) -> ServiceProbe:
        password = self._settings.qbittorrent_password
        if (
            self._settings.qbittorrent_url is None
            or self._settings.qbittorrent_username is None
            or password is None
        ):
            return ServiceProbe(service="qbittorrent", state="unconfigured")
        async with self._client() as client:
            return await QBittorrentClient(
                client,
                str(self._settings.qbittorrent_url),
                self._settings.qbittorrent_username,
                password.get_secret_value(),
            ).probe()
