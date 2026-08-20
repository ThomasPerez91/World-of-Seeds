import asyncio
from datetime import UTC, datetime
from time import monotonic

import httpx

from app import __version__
from app.core.config import Settings
from app.integrations.http import IntegrationRequestError, integration_timeout
from app.integrations.newgreedy import NewGreedyClient
from app.integrations.qbittorrent import QBittorrentClient
from app.integrations.types import (
    ExternalServicesSnapshot,
    NewGreedyOverview,
    NewGreedyStatsReset,
    NewGreedyTorrent,
    QBittorrentTorrent,
    ServiceProbe,
)


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
            cache_ttl = self._cache_ttl()
            if (
                self._cached_snapshot is not None
                and now - self._cached_at < cache_ttl
                and (not force or self._authentication_circuit_is_open())
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

    async def newgreedy_overview(self) -> NewGreedyOverview:
        base_url = self._require_newgreedy_url()
        async with self._client() as http_client:
            return await NewGreedyClient(http_client, base_url).overview()

    async def reset_newgreedy_stats(self) -> NewGreedyStatsReset:
        base_url = self._require_newgreedy_url()
        async with self._client() as http_client:
            result = await NewGreedyClient(http_client, base_url).reset_stats()
        async with self._lock:
            self._cached_snapshot = None
        return result

    async def newgreedy_torrents(self) -> list[NewGreedyTorrent]:
        base_url = self._require_newgreedy_url()
        async with self._client() as http_client:
            return await NewGreedyClient(http_client, base_url).torrents()

    async def qbittorrent_torrents(self) -> tuple[list[QBittorrentTorrent], bool]:
        snapshot = await self.snapshot()
        if snapshot.qbittorrent.state != "healthy":
            raise IntegrationRequestError("qBittorrent is unavailable")
        base_url, username, password = self._require_qbittorrent_credentials()
        async with self._client() as http_client:
            return await QBittorrentClient(
                http_client,
                base_url,
                username,
                password,
            ).torrents()

    async def qbittorrent_torrents_by_hashes(
        self, hashes: list[str]
    ) -> tuple[list[QBittorrentTorrent], bool]:
        base_url, username, password = self._require_qbittorrent_credentials()
        async with self._client() as http_client:
            return await QBittorrentClient(
                http_client,
                base_url,
                username,
                password,
            ).torrents_by_hashes(hashes)

    async def add_qbittorrent_torrent(self, content: bytes, *, save_path: str) -> None:
        base_url, username, password = self._require_qbittorrent_credentials()
        async with self._client() as http_client:
            await QBittorrentClient(
                http_client,
                base_url,
                username,
                password,
            ).add_torrent(content, save_path=save_path)

    def _authentication_circuit_is_open(self) -> bool:
        return (
            self._cached_snapshot is not None
            and self._cached_snapshot.qbittorrent.error_code == "authentication_failed"
        )

    def _cache_ttl(self) -> float:
        if self._authentication_circuit_is_open():
            return self._settings.integration_auth_failure_cache_seconds
        return self._settings.integration_health_cache_seconds

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

    def _require_newgreedy_url(self) -> str:
        if self._settings.newgreedy_url is None:
            raise IntegrationRequestError("NewGreedy is not configured")
        return str(self._settings.newgreedy_url)

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

    def _require_qbittorrent_credentials(self) -> tuple[str, str, str]:
        password = self._settings.qbittorrent_password
        if (
            self._settings.qbittorrent_url is None
            or self._settings.qbittorrent_username is None
            or password is None
        ):
            raise IntegrationRequestError("qBittorrent is not configured")
        return (
            str(self._settings.qbittorrent_url),
            self._settings.qbittorrent_username,
            password.get_secret_value(),
        )
