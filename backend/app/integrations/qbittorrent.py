import json
import math
import re
from contextlib import suppress
from time import perf_counter
from urllib.parse import urlsplit

import httpx

from app.integrations.http import (
    IntegrationAuthenticationError,
    IntegrationRequestError,
    read_limited_json,
    read_limited_text,
)
from app.integrations.types import QBittorrentTorrent, ServiceProbe

MAX_VERSION_BYTES = 1024
MAX_TORRENT_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_TORRENTS = 1_000
_TORRENT_HASH_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


class QBittorrentClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        username: str,
        password: str,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password

    async def probe(self) -> ServiceProbe:
        started_at = perf_counter()
        logged_in = False
        try:
            if not await self._login():
                return ServiceProbe(
                    service="qbittorrent",
                    state="unavailable",
                    latency_ms=_latency_ms(started_at),
                    error_code="authentication_failed",
                )
            logged_in = True
            async with self._client.stream(
                "GET",
                f"{self._base_url}/api/v2/app/version",
                headers=self._browser_headers(),
            ) as version_response:
                version_response.raise_for_status()
                version = (
                    await read_limited_text(version_response, max_bytes=MAX_VERSION_BYTES)
                ).strip()
            if version == "":
                raise ValueError("qBittorrent version response is empty")
        except (httpx.HTTPError, IntegrationRequestError, ValueError):
            return ServiceProbe(
                service="qbittorrent",
                state="unavailable",
                latency_ms=_latency_ms(started_at),
                error_code="request_failed",
            )
        finally:
            if logged_in:
                await self._logout()

        return ServiceProbe(
            service="qbittorrent",
            state="healthy",
            latency_ms=_latency_ms(started_at),
            version=version,
        )

    async def torrents(self) -> tuple[list[QBittorrentTorrent], bool]:
        return await self.torrents_by_hashes(None)

    async def torrents_by_hashes(
        self,
        hashes: list[str] | None,
    ) -> tuple[list[QBittorrentTorrent], bool]:
        logged_in = False
        try:
            if not await self._login():
                raise IntegrationAuthenticationError("qBittorrent authentication failed")
            logged_in = True
            params = {"sort": "added_on", "reverse": "true", "limit": str(MAX_TORRENTS + 1)}
            if hashes:
                if len(hashes) > MAX_TORRENTS or any(
                    _TORRENT_HASH_RE.fullmatch(value) is None for value in hashes
                ):
                    raise IntegrationRequestError("qBittorrent torrent hashes are invalid")
                params["hashes"] = "|".join(hashes)
            async with self._client.stream(
                "GET",
                f"{self._base_url}/api/v2/torrents/info",
                params=params,
                headers=self._browser_headers(),
            ) as response:
                response.raise_for_status()
                payload = await read_limited_json(
                    response,
                    max_bytes=MAX_TORRENT_RESPONSE_BYTES,
                )
        except IntegrationAuthenticationError:
            raise
        except (httpx.HTTPError, IntegrationRequestError) as exc:
            raise IntegrationRequestError("qBittorrent torrent request failed") from exc
        finally:
            if logged_in:
                await self._logout()

        if not isinstance(payload, list) or len(payload) > MAX_TORRENTS + 1:
            raise IntegrationRequestError("qBittorrent torrent payload is invalid")
        truncated = len(payload) > MAX_TORRENTS
        torrents = [_parse_torrent(item) for item in payload[:MAX_TORRENTS]]
        return torrents, truncated

    async def add_torrent(
        self,
        content: bytes,
        *,
        save_path: str,
        expected_info_hash: str,
    ) -> None:
        if not content:
            raise ValueError("Torrent content must not be empty")
        if _TORRENT_HASH_RE.fullmatch(expected_info_hash) is None:
            raise ValueError("Expected torrent hash is invalid")
        logged_in = False
        try:
            if not await self._login():
                raise IntegrationAuthenticationError("qBittorrent authentication failed")
            logged_in = True
            async with self._client.stream(
                "POST",
                f"{self._base_url}/api/v2/torrents/add",
                data={"savepath": save_path},
                files={"torrents": ("upload.torrent", content, "application/x-bittorrent")},
                headers=self._browser_headers(),
            ) as response:
                if response.status_code == 401:
                    raise IntegrationAuthenticationError("qBittorrent session expired")
                response.raise_for_status()
                if response.status_code == 204:
                    return
                result = (await read_limited_text(response, max_bytes=MAX_VERSION_BYTES)).strip()
            if result == "Ok.":
                return
            try:
                payload = json.loads(result)
            except json.JSONDecodeError as exc:
                raise IntegrationRequestError("qBittorrent rejected the torrent") from exc
            if not _accepted_add_response(payload, expected_info_hash):
                raise IntegrationRequestError("qBittorrent rejected the torrent")
        except (IntegrationAuthenticationError, IntegrationRequestError):
            raise
        except httpx.HTTPError as exc:
            raise IntegrationRequestError("qBittorrent torrent add failed") from exc
        finally:
            if logged_in:
                await self._logout()

    async def _login(self) -> bool:
        async with self._client.stream(
            "POST",
            f"{self._base_url}/api/v2/auth/login",
            data={"username": self._username, "password": self._password},
            headers=self._browser_headers(),
        ) as response:
            if response.status_code == 401:
                return False

            response.raise_for_status()

            if response.status_code == 204:
                return True

            result = await read_limited_text(
                response,
                max_bytes=MAX_VERSION_BYTES,
            )

        return result.strip() == "Ok."

    async def _logout(self) -> None:
        with suppress(httpx.HTTPError):
            async with self._client.stream(
                "POST",
                f"{self._base_url}/api/v2/auth/logout",
                headers=self._browser_headers(),
            ) as response:
                response.raise_for_status()

    def _browser_headers(self) -> dict[str, str]:
        return {
            "Origin": self._base_url,
            "Referer": f"{self._base_url}/",
        }


def _latency_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _accepted_add_response(payload: object, expected_info_hash: str) -> bool:
    if not isinstance(payload, dict):
        return False
    success_count = payload.get("success_count")
    failure_count = payload.get("failure_count")
    pending_count = payload.get("pending_count")
    added_torrent_ids = payload.get("added_torrent_ids")
    return (
        type(success_count) is int
        and success_count == 1
        and type(failure_count) is int
        and failure_count == 0
        and type(pending_count) is int
        and pending_count == 0
        and isinstance(added_torrent_ids, list)
        and added_torrent_ids == [expected_info_hash.lower()]
    )


def _parse_torrent(value: object) -> QBittorrentTorrent:
    if not isinstance(value, dict):
        raise IntegrationRequestError("qBittorrent torrent entry is invalid")
    torrent_hash = _text(value.get("hash"), "hash", minimum=40, maximum=64)
    if _TORRENT_HASH_RE.fullmatch(torrent_hash) is None:
        raise IntegrationRequestError("qBittorrent torrent hash is invalid")
    name = _text(value.get("name"), "name", minimum=1, maximum=4096)
    state = _text(value.get("state"), "state", minimum=1, maximum=64)
    progress = _number(value.get("progress"), "progress", minimum=0, maximum=1)
    size = _integer(value.get("total_size", value.get("size")), "size")
    tracker = value.get("tracker")
    category = value.get("category")
    eta = _integer(value.get("eta", 0), "eta")
    return QBittorrentTorrent(
        id=torrent_hash.lower(),
        name=name,
        state=state,
        progress=progress,
        size_bytes=size,
        downloaded_bytes=_integer(value.get("downloaded", 0), "downloaded"),
        uploaded_bytes=_integer(value.get("uploaded", 0), "uploaded"),
        download_speed_bytes=_integer(value.get("dlspeed", 0), "dlspeed"),
        upload_speed_bytes=_integer(value.get("upspeed", 0), "upspeed"),
        ratio=_number(value.get("ratio", 0), "ratio", minimum=0),
        eta_seconds=None if eta >= 8_640_000 else eta,
        category=_optional_text(category, "category", maximum=256),
        tracker_host=_tracker_host(tracker),
    )


def _text(value: object, field: str, *, minimum: int = 0, maximum: int) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise IntegrationRequestError(f"qBittorrent {field} is invalid")
    return value


def _optional_text(value: object, field: str, *, maximum: int) -> str | None:
    if value in (None, ""):
        return None
    return _text(value, field, maximum=maximum)


def _integer(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise IntegrationRequestError(f"qBittorrent {field} is invalid")
    return value


def _number(
    value: object,
    field: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntegrationRequestError(f"qBittorrent {field} is invalid")
    result = float(value)
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        raise IntegrationRequestError(f"qBittorrent {field} is invalid")
    return result


def _tracker_host(value: object) -> str | None:
    tracker = _optional_text(value, "tracker", maximum=4096)
    if tracker is None:
        return None
    try:
        hostname = urlsplit(tracker).hostname
    except ValueError as exc:
        raise IntegrationRequestError("qBittorrent tracker is invalid") from exc
    if hostname is None or len(hostname) > 253:
        return None
    return hostname.lower()
