from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from pydantic import SecretStr

from app import __version__
from app.core.config import Settings
from app.integrations.account_routing import (
    MAX_DEPLOYMENT_ACCOUNT_JSON_BYTES,
    AccountRoutingError,
    DeploymentAccountSpec,
    parse_deployment_account_specs,
)
from app.integrations.http import IntegrationRequestError, integration_timeout
from app.integrations.newgreedy import NewGreedyClient
from app.integrations.qbittorrent import QBittorrentClient
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2DesiredControl,
    QBittorrentV2Gateway,
    QBittorrentV2RunState,
)
from app.integrations.types import NewGreedyTorrent, QBittorrentTorrent


class AdminRuntimeMonitor:
    """Uncached administrator view plus ownership-checked managed controls."""

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

    async def resume_managed_torrent(
        self,
        *,
        qbittorrent_account_ref: UUID,
        info_hash: str,
        storage_key: UUID,
        download_limit_bytes_per_second: int,
    ) -> None:
        spec = next(
            (
                candidate
                for candidate in self._specs()
                if candidate.qbittorrent_account_ref == qbittorrent_account_ref
            ),
            None,
        )
        if spec is None:
            raise IntegrationRequestError("Managed qBittorrent account is unavailable")
        async with self._client() as client:
            gateway = QBittorrentV2Gateway(
                client,
                spec.qbittorrent_url,
                spec.qbittorrent_username,
                spec.qbittorrent_password.get_secret_value(),
                data_root=self._settings.qbittorrent_data_root,
            )
            await gateway.apply_managed_controls(
                (
                    QBittorrentV2DesiredControl(
                        info_hash=info_hash,
                        storage_key=storage_key,
                        run_state=QBittorrentV2RunState.RUNNING,
                        download_limit_bytes_per_second=download_limit_bytes_per_second,
                        qbittorrent_account_ref=qbittorrent_account_ref,
                    ),
                )
            )

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
            secret = self._registry_file(self._settings.integration_accounts_file)
        try:
            return parse_deployment_account_specs(secret)
        except AccountRoutingError as exc:
            raise IntegrationRequestError("Runtime integration configuration is invalid") from exc

    @staticmethod
    def _registry_file(path: Path | None) -> SecretStr:
        if path is None:
            raise IntegrationRequestError("Runtime integrations are not configured")
        try:
            if not path.is_file() or path.is_symlink():
                raise OSError
            with path.open("rb") as registry:
                content = registry.read(MAX_DEPLOYMENT_ACCOUNT_JSON_BYTES + 1)
            raw = content.decode("utf-8")
        except (OSError, UnicodeError) as exc:
            raise IntegrationRequestError("Runtime integration registry is unavailable") from exc
        if not raw or len(content) > MAX_DEPLOYMENT_ACCOUNT_JSON_BYTES:
            raise IntegrationRequestError("Runtime integration registry is invalid")
        return SecretStr(raw)

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
