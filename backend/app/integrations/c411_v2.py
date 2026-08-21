import re
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import SecretStr

from app.integrations.http import IntegrationRequestError, read_limited_json
from app.integrations.qbittorrent_v2 import QBittorrentV2AddResult
from app.torrents import normalize_torrent

MAX_NEWGREEDY_HEALTH_BYTES = 64 * 1024
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


class C411V2ConfigurationError(ValueError):
    """A required C411 V2 secret or allowlist setting is invalid."""


class NewGreedyV2UnavailableError(IntegrationRequestError):
    """The internal NewGreedy service cannot currently protect tracker traffic."""


class C411V2PayloadError(ValueError):
    """The normalized metainfo does not match the managed torrent job."""


class _ManagedTorrentAdder(Protocol):
    async def add_managed_torrent(
        self,
        content: bytes,
        *,
        expected_info_hash: str,
        storage_key: UUID,
    ) -> QBittorrentV2AddResult: ...


class _NewGreedyReadiness(Protocol):
    async def require_ready(self) -> None: ...


class NewGreedyV2Gateway:
    """Read-only worker gateway for the internal NewGreedy health contract."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        try:
            parsed = urlsplit(base_url)
            _ = parsed.port
        except ValueError as exc:
            raise C411V2ConfigurationError("NewGreedy V2 URL is invalid") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != "newgreedy"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise C411V2ConfigurationError("NewGreedy V2 must use the internal service origin")
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def require_ready(self) -> None:
        try:
            async with self._client.stream(
                "GET",
                f"{self._base_url}/api/health",
            ) as response:
                response.raise_for_status()
                payload = await read_limited_json(
                    response,
                    max_bytes=MAX_NEWGREEDY_HEALTH_BYTES,
                )
        except (httpx.HTTPError, IntegrationRequestError) as exc:
            raise NewGreedyV2UnavailableError("NewGreedy is unavailable") from exc

        total = payload.get("total") if isinstance(payload, dict) else None
        if type(total) is not int or total < 0:
            raise NewGreedyV2UnavailableError("NewGreedy returned an invalid health response")


class C411NewGreedyV2Gateway:
    """Worker-only C411 normalization immediately followed by a managed qB add."""

    def __init__(
        self,
        qbittorrent: _ManagedTorrentAdder,
        newgreedy: _NewGreedyReadiness,
        *,
        passkey: SecretStr,
        allowed_tracker_hosts: Sequence[str],
        max_total_size: int,
    ) -> None:
        secret = passkey.get_secret_value()
        if (
            not 8 <= len(secret) <= 256
            or not secret.isascii()
            or any(character in "/?#" for character in secret)
        ):
            raise C411V2ConfigurationError("C411 V2 passkey is invalid")
        hosts = tuple(dict.fromkeys(host.lower() for host in allowed_tracker_hosts))
        if not hosts:
            raise C411V2ConfigurationError("C411 V2 tracker allowlist is empty")
        if max_total_size <= 0:
            raise C411V2ConfigurationError("C411 V2 size limit must be positive")

        self._qbittorrent = qbittorrent
        self._newgreedy = newgreedy
        self._passkey = passkey
        self._allowed_tracker_hosts = hosts
        self._max_total_size = max_total_size

    async def add_torrent(
        self,
        content: bytes,
        *,
        expected_info_hash: str,
        storage_key: UUID,
    ) -> QBittorrentV2AddResult:
        if _SHA1_RE.fullmatch(expected_info_hash) is None:
            raise C411V2PayloadError("Expected torrent hash must be a canonical SHA-1 hash")

        # Check the internal proxy before injecting the infrastructure passkey, keeping the
        # normalized secret-bearing metainfo lifetime limited to the immediate qB request.
        await self._newgreedy.require_ready()
        normalized = normalize_torrent(
            content,
            passkey=self._passkey.get_secret_value(),
            allowed_tracker_hosts=list(self._allowed_tracker_hosts),
            max_total_size=self._max_total_size,
        )
        if normalized.info_hash != expected_info_hash:
            raise C411V2PayloadError("Torrent metainfo does not match the managed torrent")
        return await self._qbittorrent.add_managed_torrent(
            normalized.content,
            expected_info_hash=expected_info_hash,
            storage_key=storage_key,
        )
