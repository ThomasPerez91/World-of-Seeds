import json
import re
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from uuid import UUID

import httpx

from app.integrations.http import (
    IntegrationAuthenticationError,
    IntegrationRequestError,
    read_limited_json,
    read_limited_text,
)

WOS_V2_CATEGORY = "wos-v2"
WOS_V2_TAG = "wos-v2"
MAX_ADD_RESPONSE_BYTES = 16 * 1024
MAX_LOOKUP_RESPONSE_BYTES = 256 * 1024
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


class QBittorrentV2AddState(StrEnum):
    ADDED = "added"
    ALREADY_PRESENT = "already_present"
    RECONCILED = "reconciled"


@dataclass(frozen=True, slots=True)
class QBittorrentV2AddResult:
    state: QBittorrentV2AddState


class QBittorrentV2TransientError(IntegrationRequestError):
    """The operation can be retried without risking a duplicate add."""


class QBittorrentV2RejectedError(IntegrationRequestError):
    """qBittorrent explicitly rejected a V2 torrent add."""


class QBittorrentV2OwnershipError(IntegrationRequestError):
    """The infohash already exists but is not owned by this WOS V2 record."""


@dataclass(frozen=True, slots=True)
class _ManagedIdentity:
    save_path: str
    tags: frozenset[str]


@dataclass(frozen=True, slots=True)
class _TorrentRecord:
    info_hash: str
    save_path: str
    category: str
    tags: frozenset[str]


class _AmbiguousAdd(Exception):
    pass


class QBittorrentV2Gateway:
    """Idempotent qBittorrent writer restricted to WOS V2-owned torrents."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        username: str,
        password: str,
        *,
        data_root: Path,
    ) -> None:
        if not data_root.is_absolute():
            raise ValueError("qBittorrent data root must be absolute")
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._data_root = data_root

    async def add_managed_torrent(
        self,
        content: bytes,
        *,
        expected_info_hash: str,
        storage_key: UUID,
    ) -> QBittorrentV2AddResult:
        if not content:
            raise ValueError("Torrent content must not be empty")
        if _SHA1_RE.fullmatch(expected_info_hash) is None:
            raise ValueError("Expected torrent hash must be a canonical SHA-1 hash")

        identity = self._identity(storage_key)
        logged_in = False
        try:
            try:
                if not await self._login():
                    raise IntegrationAuthenticationError("qBittorrent authentication failed")
                logged_in = True
            except IntegrationAuthenticationError:
                raise
            except (httpx.HTTPError, IntegrationRequestError) as exc:
                raise QBittorrentV2TransientError(
                    "qBittorrent authentication request failed"
                ) from exc

            existing = await self._lookup_or_transient(expected_info_hash)
            if existing is not None:
                self._require_owned(existing, identity)
                return QBittorrentV2AddResult(QBittorrentV2AddState.ALREADY_PRESENT)

            try:
                await self._post_add(content, expected_info_hash, identity)
            except IntegrationAuthenticationError:
                raise
            except QBittorrentV2RejectedError:
                raise
            except (httpx.HTTPError, IntegrationRequestError, _AmbiguousAdd) as exc:
                return await self._reconcile_ambiguous(expected_info_hash, identity, exc)

            return QBittorrentV2AddResult(QBittorrentV2AddState.ADDED)
        finally:
            if logged_in:
                await self._logout()

    async def _post_add(
        self,
        content: bytes,
        expected_info_hash: str,
        identity: _ManagedIdentity,
    ) -> None:
        async with self._client.stream(
            "POST",
            f"{self._base_url}/api/v2/torrents/add",
            data={
                "savepath": identity.save_path,
                "category": WOS_V2_CATEGORY,
                "tags": ",".join(sorted(identity.tags)),
            },
            files={"torrents": ("upload.torrent", content, "application/x-bittorrent")},
            headers=self._browser_headers(),
        ) as response:
            if response.status_code == 401:
                raise IntegrationAuthenticationError("qBittorrent session expired")
            if 400 <= response.status_code < 500:
                raise QBittorrentV2RejectedError("qBittorrent rejected the managed torrent")
            if response.status_code >= 500:
                raise _AmbiguousAdd("qBittorrent returned an ambiguous server response")
            if not 200 <= response.status_code < 300:
                raise _AmbiguousAdd("qBittorrent returned an ambiguous response")
            if response.status_code == 204:
                return
            result = (await read_limited_text(response, max_bytes=MAX_ADD_RESPONSE_BYTES)).strip()

        if result == "Ok.":
            return
        if result == "Fails.":
            raise QBittorrentV2RejectedError("qBittorrent rejected the managed torrent")
        try:
            payload = json.loads(result)
        except json.JSONDecodeError as exc:
            raise _AmbiguousAdd("qBittorrent returned an ambiguous add payload") from exc
        if _accepted_add_response(payload, expected_info_hash):
            return
        if _explicitly_rejected_add_response(payload):
            raise QBittorrentV2RejectedError("qBittorrent rejected the managed torrent")
        raise _AmbiguousAdd("qBittorrent returned an ambiguous add payload")

    async def _reconcile_ambiguous(
        self,
        expected_info_hash: str,
        identity: _ManagedIdentity,
        cause: Exception,
    ) -> QBittorrentV2AddResult:
        try:
            existing = await self._lookup(expected_info_hash)
        except IntegrationAuthenticationError:
            raise
        except (httpx.HTTPError, IntegrationRequestError) as exc:
            raise QBittorrentV2TransientError(
                "qBittorrent add result could not be reconciled"
            ) from exc
        if existing is None:
            raise QBittorrentV2TransientError(
                "qBittorrent add result is ambiguous and the torrent is not visible"
            ) from cause
        self._require_owned(existing, identity)
        return QBittorrentV2AddResult(QBittorrentV2AddState.RECONCILED)

    async def _lookup_or_transient(self, expected_info_hash: str) -> _TorrentRecord | None:
        try:
            return await self._lookup(expected_info_hash)
        except IntegrationAuthenticationError:
            raise
        except (httpx.HTTPError, IntegrationRequestError) as exc:
            raise QBittorrentV2TransientError("qBittorrent preflight lookup failed") from exc

    async def _lookup(self, expected_info_hash: str) -> _TorrentRecord | None:
        async with self._client.stream(
            "GET",
            f"{self._base_url}/api/v2/torrents/info",
            params={"hashes": expected_info_hash},
            headers=self._browser_headers(),
        ) as response:
            if response.status_code == 401:
                raise IntegrationAuthenticationError("qBittorrent session expired")
            response.raise_for_status()
            payload = await read_limited_json(response, max_bytes=MAX_LOOKUP_RESPONSE_BYTES)

        if not isinstance(payload, list) or len(payload) > 1:
            raise IntegrationRequestError("qBittorrent managed torrent lookup is invalid")
        if not payload:
            return None
        record = _parse_torrent_record(payload[0])
        if record.info_hash != expected_info_hash:
            raise IntegrationRequestError("qBittorrent returned an unexpected torrent")
        return record

    def _identity(self, storage_key: UUID) -> _ManagedIdentity:
        storage_id = storage_key.hex
        return _ManagedIdentity(
            save_path=(self._data_root / "content" / storage_id).as_posix(),
            tags=frozenset({WOS_V2_TAG, f"wos-v2-{storage_id}"}),
        )

    @staticmethod
    def _require_owned(record: _TorrentRecord, identity: _ManagedIdentity) -> None:
        if (
            record.category != WOS_V2_CATEGORY
            or record.save_path != identity.save_path
            or not identity.tags.issubset(record.tags)
        ):
            raise QBittorrentV2OwnershipError(
                "The torrent infohash is not owned by this WOS V2 record"
            )

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
            result = await read_limited_text(response, max_bytes=MAX_ADD_RESPONSE_BYTES)
        return result.strip() == "Ok."

    async def _logout(self) -> None:
        with suppress(httpx.HTTPError, IntegrationRequestError):
            async with self._client.stream(
                "POST",
                f"{self._base_url}/api/v2/auth/logout",
                headers=self._browser_headers(),
            ) as response:
                response.raise_for_status()

    def _browser_headers(self) -> dict[str, str]:
        return {"Origin": self._base_url, "Referer": f"{self._base_url}/"}


def _accepted_add_response(payload: object, expected_info_hash: str) -> bool:
    if not isinstance(payload, dict):
        return False
    success_count = payload.get("success_count")
    failure_count = payload.get("failure_count")
    pending_count = payload.get("pending_count")
    return (
        type(success_count) is int
        and success_count == 1
        and type(failure_count) is int
        and failure_count == 0
        and type(pending_count) is int
        and pending_count == 0
        and payload.get("added_torrent_ids") == [expected_info_hash]
    )


def _explicitly_rejected_add_response(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    success_count = payload.get("success_count")
    failure_count = payload.get("failure_count")
    pending_count = payload.get("pending_count")
    return (
        type(success_count) is int
        and success_count == 0
        and type(failure_count) is int
        and failure_count > 0
        and type(pending_count) is int
        and pending_count == 0
    )


def _parse_torrent_record(value: object) -> _TorrentRecord:
    if not isinstance(value, dict):
        raise IntegrationRequestError("qBittorrent managed torrent entry is invalid")
    info_hash = value.get("hash")
    save_path = value.get("save_path")
    category = value.get("category")
    tags = value.get("tags")
    if not isinstance(info_hash, str) or _SHA1_RE.fullmatch(info_hash.lower()) is None:
        raise IntegrationRequestError("qBittorrent managed torrent hash is invalid")
    if not isinstance(save_path, str) or not 1 <= len(save_path) <= 4096:
        raise IntegrationRequestError("qBittorrent managed torrent save path is invalid")
    if not isinstance(category, str) or len(category) > 256:
        raise IntegrationRequestError("qBittorrent managed torrent category is invalid")
    if not isinstance(tags, str) or len(tags) > 4096:
        raise IntegrationRequestError("qBittorrent managed torrent tags are invalid")
    return _TorrentRecord(
        info_hash=info_hash.lower(),
        save_path=save_path,
        category=category,
        tags=frozenset(tag.strip() for tag in tags.split(",") if tag.strip()),
    )
