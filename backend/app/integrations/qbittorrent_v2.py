import json
import math
import re
from collections.abc import Sequence
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
MAX_CONTROL_TORRENTS = 200
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")


class QBittorrentV2AddState(StrEnum):
    ADDED = "added"
    ALREADY_PRESENT = "already_present"
    RECONCILED = "reconciled"


class QBittorrentV2RunState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class QBittorrentV2AddResult:
    state: QBittorrentV2AddState


@dataclass(frozen=True, slots=True)
class QBittorrentV2DesiredControl:
    info_hash: str
    storage_key: UUID
    run_state: QBittorrentV2RunState
    download_limit_bytes_per_second: int
    qbittorrent_account_ref: UUID | None = None


@dataclass(frozen=True, slots=True)
class QBittorrentV2ControlResult:
    started: tuple[str, ...]
    stopped: tuple[str, ...]
    limits_updated: tuple[str, ...]
    priorities_applied: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QBittorrentV2ManagedIdentity:
    info_hash: str
    storage_key: UUID


@dataclass(frozen=True, slots=True)
class QBittorrentV2TorrentSnapshot:
    info_hash: str
    state: str
    progress: float


@dataclass(frozen=True, slots=True)
class QBittorrentV2InventoryItem:
    info_hash: str
    storage_key: UUID | None
    claims_wos_identity: bool


@dataclass(frozen=True, slots=True)
class QBittorrentV2Inventory:
    items: tuple[QBittorrentV2InventoryItem, ...]
    truncated: bool


class QBittorrentV2TransientError(IntegrationRequestError):
    """The operation can be retried without risking a duplicate add."""


class QBittorrentV2RejectedError(IntegrationRequestError):
    """qBittorrent explicitly rejected a V2 managed-torrent operation."""


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
    state: str | None
    download_limit: int | None
    progress: float | None


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

    async def apply_managed_controls(
        self,
        controls: Sequence[QBittorrentV2DesiredControl],
    ) -> QBittorrentV2ControlResult:
        """Reconcile bounded, explicitly-owned torrents against a scheduler decision."""
        validated = _validate_controls(controls)
        if not validated:
            return QBittorrentV2ControlResult((), (), (), ())

        logged_in = False
        try:
            try:
                if not await self._login():
                    raise IntegrationAuthenticationError("qBittorrent authentication failed")
                logged_in = True
                records = await self._lookup_many(tuple(control.info_hash for control in validated))
            except IntegrationAuthenticationError:
                raise
            except (httpx.HTTPError, IntegrationRequestError) as exc:
                raise QBittorrentV2TransientError("qBittorrent control preflight failed") from exc

            by_hash = {record.info_hash: record for record in records}
            if set(by_hash) != {control.info_hash for control in validated}:
                raise QBittorrentV2TransientError("A managed torrent is not visible in qBittorrent")

            # Ownership and snapshot validation are completed for the whole batch before
            # the first mutation. This prevents a mixed batch from touching external torrents.
            try:
                for control in validated:
                    record = by_hash[control.info_hash]
                    self._require_owned(record, self._identity(control.storage_key))
                    _require_control_snapshot(record)
            except QBittorrentV2OwnershipError:
                raise
            except IntegrationRequestError as exc:
                raise QBittorrentV2TransientError(
                    "qBittorrent control snapshot is invalid"
                ) from exc

            stopped = tuple(
                control.info_hash
                for control in validated
                if control.run_state == QBittorrentV2RunState.STOPPED
                and not _is_stopped(by_hash[control.info_hash].state)
            )
            started = tuple(
                control.info_hash
                for control in validated
                if control.run_state == QBittorrentV2RunState.RUNNING
                and _is_stopped(by_hash[control.info_hash].state)
            )
            limits_updated = tuple(
                control.info_hash
                for control in validated
                if _normalized_download_limit(by_hash[control.info_hash].download_limit)
                != control.download_limit_bytes_per_second
            )
            running = tuple(
                control.info_hash
                for control in validated
                if control.run_state == QBittorrentV2RunState.RUNNING
            )

            try:
                if stopped:
                    await self._post_control("stop", {"hashes": "|".join(stopped)})
                for limit in sorted(
                    {control.download_limit_bytes_per_second for control in validated}
                ):
                    hashes = tuple(
                        control.info_hash
                        for control in validated
                        if control.info_hash in limits_updated
                        and control.download_limit_bytes_per_second == limit
                    )
                    if hashes:
                        await self._post_control(
                            "setDownloadLimit",
                            {"hashes": "|".join(hashes), "limit": str(limit)},
                        )
                # topPrio is relative. Applying it from lowest to highest recreates the
                # scheduler order after qB or WOS restarts without trusting qB integers.
                for info_hash in reversed(running):
                    await self._post_control("topPrio", {"hashes": info_hash})
                if started:
                    await self._post_control("start", {"hashes": "|".join(started)})
            except IntegrationAuthenticationError:
                raise
            except QBittorrentV2RejectedError:
                raise
            except (httpx.HTTPError, IntegrationRequestError) as exc:
                raise QBittorrentV2TransientError(
                    "qBittorrent control reconciliation failed"
                ) from exc

            return QBittorrentV2ControlResult(
                started=started,
                stopped=stopped,
                limits_updated=limits_updated,
                priorities_applied=running,
            )
        finally:
            if logged_in:
                await self._logout()

    async def inspect_managed_torrents(
        self,
        identities: Sequence[QBittorrentV2ManagedIdentity],
    ) -> tuple[QBittorrentV2TorrentSnapshot, ...]:
        """Read a bounded exact set after validating every WOS ownership marker."""

        validated = _validate_identities(identities)
        if not validated:
            return ()
        logged_in = False
        try:
            try:
                if not await self._login():
                    raise IntegrationAuthenticationError("qBittorrent authentication failed")
                logged_in = True
                records = await self._lookup_many(tuple(item.info_hash for item in validated))
            except IntegrationAuthenticationError:
                raise
            except (httpx.HTTPError, IntegrationRequestError) as exc:
                raise QBittorrentV2TransientError("qBittorrent state lookup failed") from exc

            by_hash = {record.info_hash: record for record in records}
            if set(by_hash) != {item.info_hash for item in validated}:
                raise QBittorrentV2TransientError("A managed torrent is not visible in qBittorrent")
            snapshots: list[QBittorrentV2TorrentSnapshot] = []
            for item in validated:
                record = by_hash[item.info_hash]
                self._require_owned(record, self._identity(item.storage_key))
                if (
                    record.state is None
                    or not 1 <= len(record.state) <= 64
                    or record.progress is None
                ):
                    raise QBittorrentV2TransientError("qBittorrent state snapshot is incomplete")
                snapshots.append(
                    QBittorrentV2TorrentSnapshot(
                        info_hash=record.info_hash,
                        state=record.state,
                        progress=record.progress,
                    )
                )
            return tuple(snapshots)
        finally:
            if logged_in:
                await self._logout()

    async def inventory_torrents(self, *, limit: int = 200) -> QBittorrentV2Inventory:
        """Read a bounded inventory; external torrents are classified but never mutated."""
        if not 1 <= limit <= MAX_CONTROL_TORRENTS:
            raise ValueError("qBittorrent inventory limit must be between 1 and 200")
        logged_in = False
        try:
            if not await self._login():
                raise IntegrationAuthenticationError("qBittorrent authentication failed")
            logged_in = True
            async with self._client.stream(
                "GET",
                f"{self._base_url}/api/v2/torrents/info",
                params={"limit": str(limit + 1), "offset": "0"},
                headers=self._browser_headers(),
            ) as response:
                if response.status_code == 401:
                    raise IntegrationAuthenticationError("qBittorrent session expired")
                response.raise_for_status()
                payload = await read_limited_json(response, max_bytes=MAX_LOOKUP_RESPONSE_BYTES)
            if not isinstance(payload, list) or len(payload) > limit + 1:
                raise IntegrationRequestError("qBittorrent inventory is invalid")
            records = tuple(_parse_torrent_record(value) for value in payload)
            items: list[QBittorrentV2InventoryItem] = []
            for record in records[:limit]:
                storage_key = _inventory_storage_key(record, data_root=self._data_root)
                items.append(
                    QBittorrentV2InventoryItem(
                        info_hash=record.info_hash,
                        storage_key=storage_key,
                        claims_wos_identity=(
                            record.category == WOS_V2_CATEGORY or WOS_V2_TAG in record.tags
                        ),
                    )
                )
            return QBittorrentV2Inventory(tuple(items), len(records) > limit)
        except IntegrationAuthenticationError:
            raise
        except (httpx.HTTPError, IntegrationRequestError) as exc:
            raise QBittorrentV2TransientError("qBittorrent inventory failed") from exc
        finally:
            if logged_in:
                await self._logout()

    async def remove_managed_torrent(self, identity: QBittorrentV2ManagedIdentity) -> None:
        """Remove exactly one WOS-owned torrent and its files, idempotently."""
        validated = _validate_identities((identity,))[0]
        owned_identity = self._identity(validated.storage_key)
        logged_in = False
        try:
            try:
                if not await self._login():
                    raise IntegrationAuthenticationError("qBittorrent authentication failed")
                logged_in = True
                existing = await self._lookup(validated.info_hash)
            except IntegrationAuthenticationError:
                raise
            except (httpx.HTTPError, IntegrationRequestError) as exc:
                raise QBittorrentV2TransientError("qBittorrent purge preflight failed") from exc
            if existing is None:
                return
            self._require_owned(existing, owned_identity)
            try:
                await self._post_control(
                    "delete",
                    {"hashes": validated.info_hash, "deleteFiles": "true"},
                )
            except QBittorrentV2RejectedError:
                raise
            except (httpx.HTTPError, IntegrationRequestError) as exc:
                try:
                    remaining = await self._lookup(validated.info_hash)
                except (httpx.HTTPError, IntegrationRequestError) as lookup_exc:
                    raise QBittorrentV2TransientError(
                        "qBittorrent purge result could not be reconciled"
                    ) from lookup_exc
                if remaining is None:
                    return
                self._require_owned(remaining, owned_identity)
                raise QBittorrentV2TransientError("qBittorrent purge result is ambiguous") from exc
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
                "paused": "true",
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
        records = await self._lookup_many((expected_info_hash,))
        if not records:
            return None
        return records[0]

    async def _lookup_many(self, expected_info_hashes: Sequence[str]) -> tuple[_TorrentRecord, ...]:
        async with self._client.stream(
            "GET",
            f"{self._base_url}/api/v2/torrents/info",
            params={"hashes": "|".join(expected_info_hashes)},
            headers=self._browser_headers(),
        ) as response:
            if response.status_code == 401:
                raise IntegrationAuthenticationError("qBittorrent session expired")
            response.raise_for_status()
            payload = await read_limited_json(response, max_bytes=MAX_LOOKUP_RESPONSE_BYTES)

        if not isinstance(payload, list) or len(payload) > len(expected_info_hashes):
            raise IntegrationRequestError("qBittorrent managed torrent lookup is invalid")
        records = tuple(_parse_torrent_record(value) for value in payload)
        returned_hashes = [record.info_hash for record in records]
        if len(returned_hashes) != len(set(returned_hashes)) or not set(returned_hashes).issubset(
            expected_info_hashes
        ):
            raise IntegrationRequestError("qBittorrent returned an unexpected torrent")
        return records

    async def _post_control(self, endpoint: str, data: dict[str, str]) -> None:
        async with self._client.stream(
            "POST",
            f"{self._base_url}/api/v2/torrents/{endpoint}",
            data=data,
            headers=self._browser_headers(),
        ) as response:
            if response.status_code == 401:
                raise IntegrationAuthenticationError("qBittorrent session expired")
            if 400 <= response.status_code < 500:
                raise QBittorrentV2RejectedError("qBittorrent rejected a managed torrent control")
            response.raise_for_status()

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
    state = value.get("state")
    download_limit = value.get("dl_limit")
    progress = value.get("progress")
    normalized_progress: float | None = None
    if isinstance(progress, (int, float)) and not isinstance(progress, bool):
        candidate_progress = float(progress)
        if math.isfinite(candidate_progress) and 0 <= candidate_progress <= 1:
            normalized_progress = candidate_progress
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
        state=state if isinstance(state, str) else None,
        download_limit=download_limit if type(download_limit) is int else None,
        progress=normalized_progress,
    )


def _inventory_storage_key(record: _TorrentRecord, *, data_root: Path) -> UUID | None:
    prefixes = [tag.removeprefix("wos-v2-") for tag in record.tags if tag.startswith("wos-v2-")]
    if len(prefixes) != 1 or not re.fullmatch(r"[0-9a-f]{32}", prefixes[0]):
        return None
    storage_key = UUID(hex=prefixes[0])
    expected_path = (data_root / "content" / storage_key.hex).as_posix()
    if record.category != WOS_V2_CATEGORY or record.save_path != expected_path:
        return None
    return storage_key


def _validate_identities(
    identities: Sequence[QBittorrentV2ManagedIdentity],
) -> tuple[QBittorrentV2ManagedIdentity, ...]:
    if len(identities) > MAX_CONTROL_TORRENTS:
        raise ValueError("At most 200 torrents may be inspected at once")
    validated = tuple(identities)
    hashes: list[str] = []
    for identity in validated:
        if not isinstance(identity.storage_key, UUID):
            raise ValueError("Managed torrent storage key must be a UUID")
        if (
            not isinstance(identity.info_hash, str)
            or _SHA1_RE.fullmatch(identity.info_hash) is None
        ):
            raise ValueError("Managed torrent hash must be a canonical SHA-1 hash")
        hashes.append(identity.info_hash)
    if len(hashes) != len(set(hashes)):
        raise ValueError("Each infohash may be inspected only once")
    return validated


def _validate_controls(
    controls: Sequence[QBittorrentV2DesiredControl],
) -> tuple[QBittorrentV2DesiredControl, ...]:
    if len(controls) > MAX_CONTROL_TORRENTS:
        raise ValueError("At most 200 torrents may be controlled at once")
    validated = tuple(controls)
    hashes: list[str] = []
    for control in validated:
        if not isinstance(control.storage_key, UUID):
            raise ValueError("Controlled torrent storage key must be a UUID")
        if not isinstance(control.run_state, QBittorrentV2RunState):
            raise ValueError("Controlled torrent run state is invalid")
        if not isinstance(control.info_hash, str) or _SHA1_RE.fullmatch(control.info_hash) is None:
            raise ValueError("Controlled torrent hash must be a canonical SHA-1 hash")
        if (
            type(control.download_limit_bytes_per_second) is not int
            or not 0 <= control.download_limit_bytes_per_second <= 10_000_000_000
        ):
            raise ValueError("Controlled torrent download limit is out of range")
        hashes.append(control.info_hash)
    if len(hashes) != len(set(hashes)):
        raise ValueError("Each infohash may be controlled only once")
    return validated


def _require_control_snapshot(record: _TorrentRecord) -> None:
    if record.state is None or record.download_limit is None:
        raise IntegrationRequestError("qBittorrent control snapshot is incomplete")
    normalized_state = record.state.lower()
    if normalized_state in {"error", "missingfiles", "unknown"}:
        raise IntegrationRequestError("qBittorrent torrent state cannot be controlled safely")


def _is_stopped(state: str | None) -> bool:
    return state is not None and state.lower().startswith(("stopped", "paused"))


def _normalized_download_limit(limit: int | None) -> int:
    if limit in {-1, 0}:
        return 0
    if limit is None or limit < 0:
        raise IntegrationRequestError("qBittorrent download limit is invalid")
    return limit
