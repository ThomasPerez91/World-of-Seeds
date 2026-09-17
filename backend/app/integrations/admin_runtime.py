from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from app import __version__
from app.core.config import Settings
from app.integrations.account_routing import (
    AccountRoutingError,
    DeploymentAccountSpec,
    parse_deployment_account_specs,
)
from app.integrations.http import IntegrationRequestError, integration_timeout
from app.integrations.newgreedy import NewGreedyClient
from app.integrations.qbittorrent import QBittorrentClient
from app.integrations.types import NewGreedyTorrent, QBittorrentTorrent


class AdminRuntimeMonitor:
    """Read-only, uncached view of the integration runtimes for administrators."""

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._transport = transport

    async def qbittorrent_torrents(
        self,
    ) -> tuple[datetime, list[QBittorrentTorrent], bool]:
        specs = self._specs()
        results = await asyncio.gather(*(self._qbittorrent(spec) for spec in specs))
        torrents: dict[str, QBittorrentTorrent] = {}
        truncated = False
        for items, result_truncated in results:
            truncated = truncated or result_truncated
            for item in items:
                torrents.setdefault(item.id.lower(), item)
        ordered = sorted(torrents.values(), key=lambda item: item.name.casefold())
        return datetime.now(UTC), ordered, truncated

    async def newgreedy_torrents(
        self,
    ) -> tuple[datetime, list[tuple[NewGreedyTorrent, str | None]]]:
        specs = self._specs()
        ng_results = await asyncio.gather(*(self._newgreedy(spec) for spec in specs))
        qb_results = await asyncio.gather(
            *(self._qbittorrent(spec) for spec in specs), return_exceptions=True
        )
        names: dict[str, str] = {}
        for qb_result in qb_results:
            if isinstance(qb_result, BaseException):
                continue
            for qb_torrent in qb_result[0]:
                names.setdefault(qb_torrent.id.lower(), qb_torrent.name)

        torrents: dict[str, NewGreedyTorrent] = {}
        for ng_result in ng_results:
            for ng_torrent in ng_result:
                torrents.setdefault(ng_torrent.id.lower(), ng_torrent)
        correlated = [
            (torrent, self._name_for_hash(torrent.id, names)) for torrent in torrents.values()
        ]
        return datetime.now(UTC), correlated

    async def _qbittorrent(
        self, spec: DeploymentAccountSpec
    ) -> tuple[list[QBittorrentTorrent], bool]:
        async with self._client() as client:
            return await QBittorrentClient(
                client,
                spec.qbittorrent_url,
                spec.qbittorrent_username,
                spec.qbittorrent_password.get_secret_value(),
            ).torrents()

    async def _newgreedy(self, spec: DeploymentAccountSpec) -> list[NewGreedyTorrent]:
        async with self._client() as client:
            return await NewGreedyClient(client, spec.newgreedy_url).torrents()

    def _specs(self) -> tuple[DeploymentAccountSpec, ...]:
        secret = self._settings.integration_accounts_json
        if secret is None:
            raise IntegrationRequestError("Runtime integrations are not configured")
        try:
            return parse_deployment_account_specs(secret)
        except AccountRoutingError as exc:
            raise IntegrationRequestError("Runtime integration configuration is invalid") from exc

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

    @staticmethod
    def _name_for_hash(identifier: str, names: dict[str, str]) -> str | None:
        normalized = identifier.lower()
        matches = [
            name
            for info_hash, name in names.items()
            if info_hash == normalized
            or (len(normalized) < 40 and info_hash.startswith(normalized))
        ]
        return matches[0] if len(matches) == 1 else None
