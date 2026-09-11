from __future__ import annotations

import json
import re
import secrets
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.c411_v2 import C411NewGreedyV2Gateway, NewGreedyV2Gateway
from app.integrations.qbittorrent_v2 import (
    MAX_CONTROL_TORRENTS,
    QBittorrentV2AddResult,
    QBittorrentV2ControlResult,
    QBittorrentV2DesiredControl,
    QBittorrentV2Gateway,
    QBittorrentV2ManagedIdentity,
    QBittorrentV2MissingError,
    QBittorrentV2TorrentSnapshot,
)
from app.models import ManagedTorrent
from app.options import PostgresOptionsRegistry
from app.options.registry import MAX_C411_ACCOUNTS
from app.torrents import assign_managed_torrent_account_refs

MAX_DEPLOYMENT_ACCOUNT_ROUTES = 16
MAX_DEPLOYMENT_ACCOUNT_JSON_BYTES = 64 * 1024
_QBITTORRENT_SERVICE = re.compile(r"^qbittorrent(?:-[a-z0-9]+)*$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_C411_ACCOUNT_NAMESPACE = uuid.UUID("f62d1a24-95dc-47bb-b585-9abfa93e3654")


class AccountRoutingError(RuntimeError):
    """A secret-safe deployment account routing failure."""


def c411_tracker_account_ref(slot: int) -> uuid.UUID:
    if not 1 <= slot <= MAX_C411_ACCOUNTS:
        raise ValueError("C411 account slot is invalid")
    return uuid.uuid5(_C411_ACCOUNT_NAMESPACE, f"slot:{slot}")


_C411_TRACKER_ACCOUNT_REFS = frozenset(
    c411_tracker_account_ref(slot) for slot in range(1, MAX_C411_ACCOUNTS + 1)
)


class _TorrentAdder(Protocol):
    async def add_torrent(
        self,
        content: bytes,
        *,
        expected_info_hash: str,
        storage_key: uuid.UUID,
    ) -> object: ...


class _TorrentInspector(Protocol):
    async def remove_managed_torrent(self, identity: QBittorrentV2ManagedIdentity) -> None: ...

    async def inspect_managed_torrents(
        self,
        identities: Sequence[QBittorrentV2ManagedIdentity],
    ) -> tuple[QBittorrentV2TorrentSnapshot, ...]: ...

    async def apply_managed_controls(
        self,
        controls: Sequence[QBittorrentV2DesiredControl],
    ) -> QBittorrentV2ControlResult: ...


class _SharedQBittorrent(_TorrentInspector, Protocol):
    async def add_managed_torrent(
        self,
        content: bytes,
        *,
        expected_info_hash: str,
        storage_key: uuid.UUID,
    ) -> QBittorrentV2AddResult: ...


class _NewGreedyReadiness(Protocol):
    async def require_ready(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DeploymentAccountSpec:
    qbittorrent_account_ref: uuid.UUID
    newgreedy_url: str
    qbittorrent_url: str
    qbittorrent_username: str
    qbittorrent_password: SecretStr
    legacy_tracker_account_ref: uuid.UUID | None = None
    legacy_c411_passkey: SecretStr | None = None


@dataclass(frozen=True, slots=True)
class TorrentEffectRoute:
    tracker_account_ref: uuid.UUID
    qbittorrent_account_ref: uuid.UUID
    adder: _TorrentAdder
    inspector: _TorrentInspector


@dataclass(frozen=True, slots=True)
class SharedIntegrationRoute:
    qbittorrent_account_ref: uuid.UUID
    qbittorrent: _SharedQBittorrent
    newgreedy: _NewGreedyReadiness
    allowed_tracker_hosts: tuple[str, ...]
    max_total_size: int
    legacy_tracker_account_ref: uuid.UUID | None = None
    legacy_c411_passkey: SecretStr | None = None


@dataclass(frozen=True, slots=True)
class _SessionRoutes:
    selectable: tuple[TorrentEffectRoute, ...]
    legacy_replacement: TorrentEffectRoute | None


class DeploymentAccountRouter:
    """Resolve immutable SQL references to deployment-only integration clients."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        routes: Sequence[TorrentEffectRoute] = (),
        *,
        shared_integration: SharedIntegrationRoute | None = None,
        random_index: Callable[[int], int] = secrets.randbelow,
    ) -> None:
        if bool(routes) == (shared_integration is not None):
            raise AccountRoutingError("deployment_account_route_count_invalid")
        ordered = tuple(
            sorted(
                routes,
                key=lambda route: (
                    route.tracker_account_ref.bytes,
                    route.qbittorrent_account_ref.bytes,
                ),
            )
        )
        if shared_integration is None and not 1 <= len(ordered) <= MAX_DEPLOYMENT_ACCOUNT_ROUTES:
            raise AccountRoutingError("deployment_account_route_count_invalid")
        tracker_refs = [route.tracker_account_ref for route in ordered]
        qb_refs = [route.qbittorrent_account_ref for route in ordered]
        if (
            any(reference.int == 0 for reference in (*tracker_refs, *qb_refs))
            or len(tracker_refs) != len(set(tracker_refs))
            or len(qb_refs) != len(set(qb_refs))
        ):
            raise AccountRoutingError("deployment_account_reference_invalid")
        self._session_factory = session_factory
        self._ordered = ordered
        self._shared = shared_integration
        self._random_index = random_index
        self._legacy_route: TorrentEffectRoute | None = None
        self._qb_by_ref: dict[uuid.UUID, _TorrentInspector]
        if shared_integration is not None:
            legacy_ref = shared_integration.legacy_tracker_account_ref
            legacy_passkey = shared_integration.legacy_c411_passkey
            if (legacy_ref is None) != (legacy_passkey is None) or (
                legacy_ref is not None and legacy_ref.int == 0
            ):
                raise AccountRoutingError("deployment_legacy_account_route_invalid")
            if legacy_ref is not None and legacy_passkey is not None:
                self._legacy_route = self._shared_route(legacy_ref, legacy_passkey)
            self._qb_by_ref = {
                shared_integration.qbittorrent_account_ref: shared_integration.qbittorrent
            }
            self._by_pair: dict[tuple[uuid.UUID, uuid.UUID], TorrentEffectRoute] = {}
            return
        self._by_pair = {
            (route.tracker_account_ref, route.qbittorrent_account_ref): route for route in ordered
        }
        self._qb_by_ref = {route.qbittorrent_account_ref: route.inspector for route in ordered}

    async def resolve(
        self,
        managed_torrent_id: uuid.UUID,
        info_hash: str,
    ) -> TorrentEffectRoute:
        if _SHA1.fullmatch(info_hash) is None:
            raise AccountRoutingError("managed_torrent_route_invalid")
        async with self._session_factory() as session, session.begin():
            torrent = await session.scalar(
                select(ManagedTorrent)
                .where(ManagedTorrent.id == managed_torrent_id)
                .with_for_update()
            )
            if torrent is None or torrent.info_hash != info_hash:
                raise AccountRoutingError("managed_torrent_route_invalid")
            session_routes = await self._routes_for_session(session)
            routes = session_routes.selectable
            tracker_ref = torrent.tracker_account_ref
            qb_ref = torrent.qbittorrent_account_ref
            if (tracker_ref is None) != (qb_ref is None):
                raise AccountRoutingError("managed_torrent_route_incomplete")
            if tracker_ref is None or qb_ref is None:
                if not routes:
                    raise AccountRoutingError("c411_account_unconfigured")
                route = routes[self._random_index(len(routes))]
                await assign_managed_torrent_account_refs(
                    session,
                    torrent.id,
                    tracker_account_ref=route.tracker_account_ref,
                    qbittorrent_account_ref=route.qbittorrent_account_ref,
                )
                return route
            assigned_route = {
                (route.tracker_account_ref, route.qbittorrent_account_ref): route
                for route in routes
            }.get((tracker_ref, qb_ref))
            if (
                assigned_route is None
                and self._legacy_route is not None
                and (tracker_ref, qb_ref)
                == (
                    self._legacy_route.tracker_account_ref,
                    self._legacy_route.qbittorrent_account_ref,
                )
            ):
                replacement = session_routes.legacy_replacement
                if replacement is None:
                    return self._legacy_route
                torrent.tracker_account_ref = replacement.tracker_account_ref
                torrent.qbittorrent_account_ref = replacement.qbittorrent_account_ref
                await session.flush()
                return replacement
            if (
                assigned_route is None
                and self._shared is not None
                and qb_ref == self._shared.qbittorrent_account_ref
                and tracker_ref not in _C411_TRACKER_ACCOUNT_REFS
                and routes
            ):
                replacement = routes[self._random_index(len(routes))]
                torrent.tracker_account_ref = replacement.tracker_account_ref
                torrent.qbittorrent_account_ref = replacement.qbittorrent_account_ref
                await session.flush()
                return replacement
            if assigned_route is None:
                raise AccountRoutingError("managed_torrent_route_unavailable")
            return assigned_route

    async def _routes_for_session(self, session: AsyncSession) -> _SessionRoutes:
        if self._shared is None:
            return _SessionRoutes(self._ordered, None)
        values = await PostgresOptionsRegistry().snapshot(session)
        routes: list[TorrentEffectRoute] = []
        legacy_replacement: TorrentEffectRoute | None = None
        legacy_passkey = self._shared.legacy_c411_passkey
        for slot in range(1, MAX_C411_ACCOUNTS + 1):
            number = values[f"WOS_C411_ACCOUNT_{slot:02d}_NUMBER"]
            passkey = values[f"WOS_C411_ACCOUNT_{slot:02d}_PASSKEY"]
            if not isinstance(number, str) or not isinstance(passkey, str):
                raise AccountRoutingError("c411_account_config_invalid")
            if not number:
                continue
            route = self._shared_route(c411_tracker_account_ref(slot), SecretStr(passkey))
            routes.append(route)
            if legacy_passkey is not None and secrets.compare_digest(
                passkey,
                legacy_passkey.get_secret_value(),
            ):
                legacy_replacement = route
        if not routes and self._legacy_route is not None:
            routes.append(self._legacy_route)
        return _SessionRoutes(tuple(routes), legacy_replacement)

    def _shared_route(
        self,
        tracker_account_ref: uuid.UUID,
        passkey: SecretStr,
    ) -> TorrentEffectRoute:
        if self._shared is None:
            raise AccountRoutingError("deployment_shared_integration_required")
        return TorrentEffectRoute(
            tracker_account_ref=tracker_account_ref,
            qbittorrent_account_ref=self._shared.qbittorrent_account_ref,
            adder=C411NewGreedyV2Gateway(
                self._shared.qbittorrent,
                self._shared.newgreedy,
                passkey=passkey,
                allowed_tracker_hosts=self._shared.allowed_tracker_hosts,
                max_total_size=self._shared.max_total_size,
            ),
            inspector=self._shared.qbittorrent,
        )

    async def apply_managed_controls(
        self,
        controls: Sequence[QBittorrentV2DesiredControl],
    ) -> QBittorrentV2ControlResult:
        if len(controls) > MAX_CONTROL_TORRENTS:
            raise AccountRoutingError("qbittorrent_control_set_too_large")
        info_hashes = [control.info_hash for control in controls]
        if len(info_hashes) != len(set(info_hashes)):
            raise AccountRoutingError("qbittorrent_control_route_invalid")
        groups: dict[uuid.UUID, list[QBittorrentV2DesiredControl]] = {}
        for control in controls:
            account_ref = control.qbittorrent_account_ref
            if account_ref is None or account_ref not in self._qb_by_ref:
                raise AccountRoutingError("qbittorrent_control_route_unavailable")
            groups.setdefault(account_ref, []).append(control)

        started: list[str] = []
        stopped: list[str] = []
        limits_updated: list[str] = []
        priorities_applied: list[str] = []
        for account_ref in sorted(groups, key=lambda value: value.bytes):
            result = await self._qb_by_ref[account_ref].apply_managed_controls(groups[account_ref])
            started.extend(result.started)
            stopped.extend(result.stopped)
            limits_updated.extend(result.limits_updated)
            priorities_applied.extend(result.priorities_applied)
        return QBittorrentV2ControlResult(
            tuple(started),
            tuple(stopped),
            tuple(limits_updated),
            tuple(priorities_applied),
        )

    async def managed_torrent_is_present(
        self,
        qbittorrent_account_ref: uuid.UUID,
        identity: QBittorrentV2ManagedIdentity,
    ) -> bool:
        inspector = self._qb_by_ref.get(qbittorrent_account_ref)
        if inspector is None:
            raise AccountRoutingError("qbittorrent_control_route_unavailable")
        try:
            snapshots = await inspector.inspect_managed_torrents((identity,))
        except QBittorrentV2MissingError:
            return False
        if len(snapshots) != 1:
            raise AccountRoutingError("qbittorrent_inventory_invalid")
        return True


def parse_deployment_account_specs(secret: SecretStr) -> tuple[DeploymentAccountSpec, ...]:
    raw = secret.get_secret_value()
    if not raw or len(raw.encode("utf-8")) > MAX_DEPLOYMENT_ACCOUNT_JSON_BYTES:
        raise AccountRoutingError("deployment_account_config_invalid")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AccountRoutingError("deployment_account_config_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"routes"}:
        raise AccountRoutingError("deployment_account_config_invalid")
    routes = payload["routes"]
    if not isinstance(routes, list) or not 1 <= len(routes) <= MAX_DEPLOYMENT_ACCOUNT_ROUTES:
        raise AccountRoutingError("deployment_account_config_invalid")

    specs: list[DeploymentAccountSpec] = []
    expected_keys = {
        "qbittorrent_account_ref",
        "newgreedy_url",
        "qbittorrent_url",
        "qbittorrent_username",
        "qbittorrent_password",
    }
    legacy_keys = {*expected_keys, "tracker_account_ref", "c411_passkey"}
    try:
        for value in routes:
            if not isinstance(value, dict) or frozenset(value) not in {
                frozenset(expected_keys),
                frozenset(legacy_keys),
            }:
                raise ValueError
            qb_ref = uuid.UUID(_required_string(value, "qbittorrent_account_ref", 36))
            newgreedy_url = _internal_origin(
                _required_string(value, "newgreedy_url", 512),
                service="newgreedy",
            )
            qbittorrent_url = _internal_origin(
                _required_string(value, "qbittorrent_url", 512),
                service="qbittorrent",
            )
            username = _required_string(value, "qbittorrent_username", 128)
            password = _required_string(value, "qbittorrent_password", 1024)
            legacy_tracker_ref: uuid.UUID | None = None
            legacy_passkey: SecretStr | None = None
            if frozenset(value) == frozenset(legacy_keys):
                legacy_tracker_ref = uuid.UUID(_required_string(value, "tracker_account_ref", 36))
                legacy_passkey_value = _required_string(value, "c411_passkey", 256)
                if legacy_tracker_ref.int == 0 or not 8 <= len(legacy_passkey_value) <= 256:
                    raise ValueError
                legacy_passkey = SecretStr(legacy_passkey_value)
            if qb_ref.int == 0:
                raise ValueError
            specs.append(
                DeploymentAccountSpec(
                    qb_ref,
                    newgreedy_url,
                    qbittorrent_url,
                    username,
                    SecretStr(password),
                    legacy_tracker_ref,
                    legacy_passkey,
                )
            )
    except (TypeError, ValueError) as exc:
        raise AccountRoutingError("deployment_account_config_invalid") from exc
    return tuple(specs)


def build_deployment_account_router(
    secret: SecretStr,
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    allowed_tracker_hosts: list[str],
    data_root: Path,
    max_total_size: int,
) -> DeploymentAccountRouter:
    specs = parse_deployment_account_specs(secret)
    if len(specs) != 1:
        raise AccountRoutingError("deployment_shared_integration_required")
    primary = specs[0]
    qbittorrent = QBittorrentV2Gateway(
        client,
        primary.qbittorrent_url,
        primary.qbittorrent_username,
        primary.qbittorrent_password.get_secret_value(),
        data_root=data_root,
    )
    return DeploymentAccountRouter(
        session_factory,
        shared_integration=SharedIntegrationRoute(
            qbittorrent_account_ref=primary.qbittorrent_account_ref,
            qbittorrent=qbittorrent,
            newgreedy=NewGreedyV2Gateway(client, primary.newgreedy_url),
            allowed_tracker_hosts=tuple(allowed_tracker_hosts),
            max_total_size=max_total_size,
            legacy_tracker_account_ref=primary.legacy_tracker_account_ref,
            legacy_c411_passkey=primary.legacy_c411_passkey,
        ),
    )


def _required_string(value: dict[object, object], key: str, maximum: int) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not 1 <= len(candidate) <= maximum:
        raise ValueError
    return candidate


def _internal_origin(value: str, *, service: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError from exc
    hostname = parsed.hostname
    valid_host = (
        hostname == "newgreedy"
        if service == "newgreedy"
        else bool(hostname and _QBITTORRENT_SERVICE.fullmatch(hostname))
    )
    if (
        parsed.scheme not in {"http", "https"}
        or not valid_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise ValueError
    return value.rstrip("/")
