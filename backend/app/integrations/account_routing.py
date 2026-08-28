from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
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
    QBittorrentV2ControlResult,
    QBittorrentV2DesiredControl,
    QBittorrentV2Gateway,
    QBittorrentV2ManagedIdentity,
    QBittorrentV2MissingError,
    QBittorrentV2TorrentSnapshot,
)
from app.models import ManagedTorrent
from app.torrents import assign_managed_torrent_account_refs

MAX_DEPLOYMENT_ACCOUNT_ROUTES = 16
MAX_DEPLOYMENT_ACCOUNT_JSON_BYTES = 64 * 1024
_QBITTORRENT_SERVICE = re.compile(r"^qbittorrent(?:-[a-z0-9]+)*$")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")


class AccountRoutingError(RuntimeError):
    """A secret-safe deployment account routing failure."""


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


@dataclass(frozen=True, slots=True)
class DeploymentAccountSpec:
    tracker_account_ref: uuid.UUID
    qbittorrent_account_ref: uuid.UUID
    newgreedy_url: str
    c411_passkey: SecretStr
    qbittorrent_url: str
    qbittorrent_username: str
    qbittorrent_password: SecretStr


@dataclass(frozen=True, slots=True)
class TorrentEffectRoute:
    tracker_account_ref: uuid.UUID
    qbittorrent_account_ref: uuid.UUID
    adder: _TorrentAdder
    inspector: _TorrentInspector


class DeploymentAccountRouter:
    """Resolve immutable SQL references to deployment-only integration clients."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        routes: Sequence[TorrentEffectRoute],
    ) -> None:
        ordered = tuple(
            sorted(
                routes,
                key=lambda route: (
                    route.tracker_account_ref.bytes,
                    route.qbittorrent_account_ref.bytes,
                ),
            )
        )
        if not 1 <= len(ordered) <= MAX_DEPLOYMENT_ACCOUNT_ROUTES:
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
            tracker_ref = torrent.tracker_account_ref
            qb_ref = torrent.qbittorrent_account_ref
            if (tracker_ref is None) != (qb_ref is None):
                raise AccountRoutingError("managed_torrent_route_incomplete")
            if tracker_ref is None or qb_ref is None:
                route = self._ordered[int(info_hash, 16) % len(self._ordered)]
                await assign_managed_torrent_account_refs(
                    session,
                    torrent.id,
                    tracker_account_ref=route.tracker_account_ref,
                    qbittorrent_account_ref=route.qbittorrent_account_ref,
                )
                return route
            assigned_route = self._by_pair.get((tracker_ref, qb_ref))
            if assigned_route is None:
                raise AccountRoutingError("managed_torrent_route_unavailable")
            return assigned_route

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
        "tracker_account_ref",
        "qbittorrent_account_ref",
        "newgreedy_url",
        "c411_passkey",
        "qbittorrent_url",
        "qbittorrent_username",
        "qbittorrent_password",
    }
    try:
        for value in routes:
            if not isinstance(value, dict) or set(value) != expected_keys:
                raise ValueError
            tracker_ref = uuid.UUID(_required_string(value, "tracker_account_ref", 36))
            qb_ref = uuid.UUID(_required_string(value, "qbittorrent_account_ref", 36))
            newgreedy_url = _internal_origin(
                _required_string(value, "newgreedy_url", 512),
                service="newgreedy",
            )
            qbittorrent_url = _internal_origin(
                _required_string(value, "qbittorrent_url", 512),
                service="qbittorrent",
            )
            passkey = _required_string(value, "c411_passkey", 256)
            username = _required_string(value, "qbittorrent_username", 128)
            password = _required_string(value, "qbittorrent_password", 1024)
            if tracker_ref.int == 0 or qb_ref.int == 0 or not 8 <= len(passkey) <= 256:
                raise ValueError
            specs.append(
                DeploymentAccountSpec(
                    tracker_ref,
                    qb_ref,
                    newgreedy_url,
                    SecretStr(passkey),
                    qbittorrent_url,
                    username,
                    SecretStr(password),
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
    routes: list[TorrentEffectRoute] = []
    for spec in parse_deployment_account_specs(secret):
        qbittorrent = QBittorrentV2Gateway(
            client,
            spec.qbittorrent_url,
            spec.qbittorrent_username,
            spec.qbittorrent_password.get_secret_value(),
            data_root=data_root,
        )
        routes.append(
            TorrentEffectRoute(
                tracker_account_ref=spec.tracker_account_ref,
                qbittorrent_account_ref=spec.qbittorrent_account_ref,
                adder=C411NewGreedyV2Gateway(
                    qbittorrent,
                    NewGreedyV2Gateway(client, spec.newgreedy_url),
                    passkey=spec.c411_passkey,
                    allowed_tracker_hosts=allowed_tracker_hosts,
                    max_total_size=max_total_size,
                ),
                inspector=qbittorrent,
            )
        )
    return DeploymentAccountRouter(session_factory, routes)


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
