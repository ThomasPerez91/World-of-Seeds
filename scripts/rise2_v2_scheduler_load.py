#!/usr/bin/env python3
"""Secret-free Rise2 scheduler load harness for the V2-33 limited pilot."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import engine, session_factory
from app.integrations.account_routing import (
    DeploymentAccountSpec,
    build_deployment_account_router,
    parse_deployment_account_specs,
)
from app.integrations.qbittorrent_v2 import QBittorrentV2Gateway, QBittorrentV2ManagedIdentity
from app.jobs.torrent_payloads import MAX_MANAGED_TORRENT_BYTES
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    SchedulerState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.options import PostgresOptionsRegistry
from app.scheduler.runtime import SchedulerRuntime

SCHEMA = "world-of-seeds-v2-rise2-scheduler-load/v1"
CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")
ACCOUNT_COUNT = 100
ELIGIBLE_TORRENT_COUNT = 205
COOLDOWN_TORRENT_COUNT = 2
READY_TORRENT_COUNT = 2
TOTAL_TORRENT_COUNT = ELIGIBLE_TORRENT_COUNT + COOLDOWN_TORRENT_COUNT + READY_TORRENT_COUNT
LOCAL_ANNOUNCE = b"http://127.0.0.1:1/announce"
PIECE_LENGTH = 16 * 1024 * 1024
PILOT_NAMESPACE = uuid.UUID("6a2c748c-3294-46b4-a5fb-76a645a125b4")


@dataclass(frozen=True, slots=True)
class Fixture:
    index: int
    info_hash: str
    storage_key: uuid.UUID
    metainfo: bytes
    scheduler_size: int
    kind: str
    route_index: int


def _campaign(value: str) -> str:
    if CAMPAIGN_RE.fullmatch(value) is None:
        raise ValueError("campaign must match [a-z0-9][a-z0-9-]{0,15}")
    return value


def _prefix(campaign: str) -> str:
    return f"v233-{campaign}-"


def _torrent_name(campaign: str, index: int) -> str:
    return f"{_prefix(campaign)}torrent-{index:03d}"


def _username(campaign: str, index: int) -> str:
    return f"{_prefix(campaign)}u{index:03d}"


def _bencode(value: Any) -> bytes:
    if type(value) is int:
        return b"i" + str(value).encode("ascii") + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        keys = sorted(value)
        if any(not isinstance(key, bytes) for key in keys):
            raise TypeError("bencode dictionary keys must be bytes")
        return b"d" + b"".join(_bencode(key) + _bencode(value[key]) for key in keys) + b"e"
    raise TypeError("unsupported bencode value")


def _scheduler_size(index: int) -> int:
    if index < 2:
        return 64 * 1024**3
    if index < 6:
        return 20 * 1024**3
    return 1 * 1024**2 + index


def _kind(index: int) -> str:
    if index < ELIGIBLE_TORRENT_COUNT:
        return "eligible"
    if index < ELIGIBLE_TORRENT_COUNT + COOLDOWN_TORRENT_COUNT:
        return "cooldown"
    return "ready"


def _fixture(campaign: str, index: int, route_count: int) -> Fixture:
    if route_count < 1:
        raise ValueError("route_count must be positive")
    filename = f"pilot-{campaign}-{index:03d}.bin"
    info = {
        b"length": 1,
        b"name": filename.encode("ascii"),
        b"piece length": PIECE_LENGTH,
        b"pieces": hashlib.sha1(f"{campaign}:{index}".encode("ascii")).digest(),
        b"private": 1,
    }
    info_bytes = _bencode(info)
    return Fixture(
        index=index,
        info_hash=hashlib.sha1(info_bytes).hexdigest(),
        storage_key=uuid.uuid5(PILOT_NAMESPACE, f"{campaign}:{index}"),
        metainfo=_bencode({b"announce": LOCAL_ANNOUNCE, b"info": info}),
        scheduler_size=_scheduler_size(index),
        kind=_kind(index),
        route_index=index % route_count,
    )


def _fixtures(campaign: str, route_count: int) -> tuple[Fixture, ...]:
    return tuple(_fixture(campaign, index, route_count) for index in range(TOTAL_TORRENT_COUNT))


def _percentile(samples: Sequence[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
    return ordered[index]


def _safe_runtime() -> tuple[Any, tuple[DeploymentAccountSpec, ...]]:
    settings = get_settings()
    if settings.environment != "production" or os.environ.get("WOS_RUNTIME_PROFILE") != "v2":
        raise RuntimeError("Rise2 pilot load harness requires the production V2 profile")
    if os.environ.get("WOS_RISE2_PILOT_ACK") != "V2-33":
        raise RuntimeError("WOS_RISE2_PILOT_ACK=V2-33 is required")
    if settings.integration_accounts_json is None:
        raise RuntimeError("Rise2 pilot load harness requires deployment account routing")
    specs = parse_deployment_account_specs(settings.integration_accounts_json)
    if Path(settings.data_root) != Path("/data") or Path(settings.qbittorrent_data_root) != Path(
        "/data"
    ):
        raise RuntimeError("Rise2 pilot load harness requires the isolated /data root")
    return settings, specs


def _gateways(
    client: httpx.AsyncClient,
    specs: Sequence[DeploymentAccountSpec],
    *,
    data_root: Path,
) -> tuple[QBittorrentV2Gateway, ...]:
    return tuple(
        QBittorrentV2Gateway(
            client,
            spec.qbittorrent_url,
            spec.qbittorrent_username,
            spec.qbittorrent_password.get_secret_value(),
            data_root=data_root,
        )
        for spec in specs
    )


async def _campaign_counts(session: AsyncSession, campaign: str) -> dict[str, int]:
    prefix = _prefix(campaign)
    users = await session.scalar(
        select(func.count()).select_from(User).where(User.username.like(f"{prefix}%"))
    )
    torrents = await session.scalar(
        select(func.count())
        .select_from(ManagedTorrent)
        .where(ManagedTorrent.name.like(f"{prefix}%"))
    )
    requests = await session.scalar(
        select(func.count())
        .select_from(TorrentRequest)
        .join(ManagedTorrent)
        .where(ManagedTorrent.name.like(f"{prefix}%"))
    )
    return {
        "users": int(users or 0),
        "torrents": int(torrents or 0),
        "requests": int(requests or 0),
    }


async def _assert_campaign_absent(campaign: str) -> None:
    async with session_factory() as session:
        counts = await _campaign_counts(session, campaign)
        await session.rollback()
    if any(counts.values()):
        raise RuntimeError("campaign already exists in PostgreSQL")


async def _add_qbittorrent_fixtures(
    fixtures: Sequence[Fixture],
    specs: Sequence[DeploymentAccountSpec],
    *,
    data_root: Path,
) -> None:
    timeout = httpx.Timeout(connect=10, read=30, write=30, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        gateways = _gateways(client, specs, data_root=data_root)
        added: list[Fixture] = []
        try:
            for position, fixture in enumerate(fixtures, start=1):
                await gateways[fixture.route_index].add_managed_torrent(
                    fixture.metainfo,
                    expected_info_hash=fixture.info_hash,
                    storage_key=fixture.storage_key,
                )
                added.append(fixture)
                if position % 25 == 0 or position == len(fixtures):
                    print(
                        f"prepare_qbittorrent={position}/{len(fixtures)}",
                        file=sys.stderr,
                        flush=True,
                    )
        except Exception:
            for fixture in reversed(added):
                with suppress(Exception):
                    await gateways[fixture.route_index].remove_managed_torrent(
                        QBittorrentV2ManagedIdentity(fixture.info_hash, fixture.storage_key)
                    )
            raise


async def _remove_qbittorrent_fixtures(
    fixtures: Sequence[Fixture],
    specs: Sequence[DeploymentAccountSpec],
    *,
    data_root: Path,
) -> None:
    timeout = httpx.Timeout(connect=10, read=30, write=30, pool=10)
    async with httpx.AsyncClient(timeout=timeout) as client:
        gateways = _gateways(client, specs, data_root=data_root)
        for fixture in fixtures:
            await gateways[fixture.route_index].remove_managed_torrent(
                QBittorrentV2ManagedIdentity(fixture.info_hash, fixture.storage_key)
            )


async def _insert_campaign(
    campaign: str,
    fixtures: Sequence[Fixture],
    specs: Sequence[DeploymentAccountSpec],
) -> dict[str, int]:
    now = datetime.now(UTC)
    users: list[User] = []
    async with session_factory() as session, session.begin():
        if any((await _campaign_counts(session, campaign)).values()):
            raise RuntimeError("campaign appeared while preparing PostgreSQL")
        for index in range(ACCOUNT_COUNT):
            user = User(
                username=_username(campaign, index),
                password_hash="v2-33-pilot-disabled-password",
                is_admin=index == 0,
                is_active=True,
                must_change_credentials=True,
            )
            session.add(user)
            users.append(user)
        await session.flush()

        request_count = 0
        for fixture in fixtures:
            spec = specs[fixture.route_index]
            state = (
                ManagedTorrentState.READY
                if fixture.kind == "ready"
                else ManagedTorrentState.PAUSED
            )
            torrent = ManagedTorrent(
                info_hash=fixture.info_hash,
                storage_key=fixture.storage_key,
                name=_torrent_name(campaign, fixture.index),
                total_size=fixture.scheduler_size,
                state=state,
                qb_state="pausedDL",
                progress=1.0 if fixture.kind == "ready" else 0.0,
                tracker_account_ref=spec.tracker_account_ref,
                qbittorrent_account_ref=spec.qbittorrent_account_ref,
                scheduler_retry_at=(
                    now + timedelta(hours=2) if fixture.kind == "cooldown" else None
                ),
                ready_at=now if fixture.kind == "ready" else None,
                retention_expires_at=(
                    now + timedelta(days=30) if fixture.kind == "ready" else None
                ),
                desired_active=False,
                desired_priority=None,
                created_at=now + timedelta(microseconds=fixture.index),
                updated_at=now,
            )
            session.add(torrent)
            await session.flush()

            owner = users[fixture.index % ACCOUNT_COUNT]
            session.add(
                TorrentRequest(
                    user_id=owner.id,
                    managed_torrent_id=torrent.id,
                    state=(
                        TorrentRequestState.READY
                        if fixture.kind == "ready"
                        else TorrentRequestState.REQUESTED
                    ),
                    ready_at=now if fixture.kind == "ready" else None,
                    created_at=now + timedelta(microseconds=fixture.index),
                    updated_at=now,
                )
            )
            request_count += 1
            if fixture.kind == "eligible" and fixture.index < 5:
                shared = users[(fixture.index + 1) % ACCOUNT_COUNT]
                session.add(
                    TorrentRequest(
                        user_id=shared.id,
                        managed_torrent_id=torrent.id,
                        state=TorrentRequestState.REQUESTED,
                        created_at=now + timedelta(microseconds=fixture.index + 500),
                        updated_at=now,
                    )
                )
                request_count += 1

        await session.flush()
        counts = await _campaign_counts(session, campaign)
        if counts["users"] != ACCOUNT_COUNT or counts["torrents"] != TOTAL_TORRENT_COUNT:
            raise RuntimeError("prepared campaign counts are inconsistent")
        if counts["requests"] != request_count:
            raise RuntimeError("prepared request count is inconsistent")
    return counts


async def prepare(campaign: str) -> dict[str, object]:
    settings, specs = _safe_runtime()
    campaign = _campaign(campaign)
    fixtures = _fixtures(campaign, len(specs))
    await _assert_campaign_absent(campaign)
    await _add_qbittorrent_fixtures(
        fixtures,
        specs,
        data_root=Path(settings.qbittorrent_data_root),
    )
    try:
        counts = await _insert_campaign(campaign, fixtures, specs)
    except Exception:
        await _remove_qbittorrent_fixtures(
            fixtures,
            specs,
            data_root=Path(settings.qbittorrent_data_root),
        )
        raise
    return {
        "schema": SCHEMA,
        "phase": "prepare",
        "campaign": campaign,
        "account_count": counts["users"],
        "managed_torrent_count": counts["torrents"],
        "request_count": counts["requests"],
        "eligible_torrent_count": ELIGIBLE_TORRENT_COUNT,
        "cooldown_torrent_count": COOLDOWN_TORRENT_COUNT,
        "ready_torrent_count": READY_TORRENT_COUNT,
        "backlog_gt_200": ELIGIBLE_TORRENT_COUNT > 200,
        "external_tracker_requests": 0,
        "secrets_or_business_identifiers_in_report": False,
    }


async def _campaign_maps(
    campaign: str,
) -> tuple[dict[uuid.UUID, set[uuid.UUID]], set[uuid.UUID], uuid.UUID]:
    prefix = _prefix(campaign)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    ManagedTorrent.id,
                    ManagedTorrent.state,
                    ManagedTorrent.scheduler_retry_at,
                    TorrentRequest.user_id,
                )
                .join(TorrentRequest)
                .where(
                    ManagedTorrent.name.like(f"{prefix}%"),
                    TorrentRequest.state.in_(
                        (TorrentRequestState.REQUESTED, TorrentRequestState.ACTIVE)
                    ),
                )
            )
        ).all()
        actor = await session.scalar(
            select(User.id).where(
                User.username == _username(campaign, 0),
                User.is_admin.is_(True),
                User.is_active.is_(True),
            )
        )
        await session.rollback()
    if actor is None:
        raise RuntimeError("pilot option actor is unavailable")

    mapping: dict[uuid.UUID, set[uuid.UUID]] = {}
    users: set[uuid.UUID] = set()
    now = datetime.now(UTC)
    for torrent_id, state, retry_at, user_id in rows:
        if state not in (ManagedTorrentState.PAUSED, ManagedTorrentState.DOWNLOADING):
            continue
        if retry_at is not None:
            retry = retry_at if retry_at.tzinfo is not None else retry_at.replace(tzinfo=UTC)
            if retry > now:
                continue
        mapping.setdefault(torrent_id, set()).add(user_id)
        users.add(user_id)
    if len(users) != ACCOUNT_COUNT:
        raise RuntimeError("every pilot account must own an eligible scheduler request")
    if len(mapping) != ELIGIBLE_TORRENT_COUNT:
        raise RuntimeError("eligible scheduler backlog is incomplete")
    return mapping, users, actor


async def _reject_non_pilot_scheduler_candidates(campaign: str) -> None:
    prefix = _prefix(campaign)
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count(func.distinct(ManagedTorrent.id)))
            .select_from(ManagedTorrent)
            .join(TorrentRequest)
            .join(User, User.id == TorrentRequest.user_id)
            .where(
                ManagedTorrent.state.in_(
                    (ManagedTorrentState.PAUSED, ManagedTorrentState.DOWNLOADING)
                ),
                ~ManagedTorrent.name.like(f"{prefix}%"),
                TorrentRequest.state.in_(
                    (TorrentRequestState.REQUESTED, TorrentRequestState.ACTIVE)
                ),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
        )
        await session.rollback()
    if int(count or 0) != 0:
        raise RuntimeError("non-pilot scheduler candidates are present")


async def _set_slots(
    actor_user_id: uuid.UUID,
    slots: int,
) -> tuple[PostgresOptionsRegistry, int, int]:
    registry = PostgresOptionsRegistry()
    async with session_factory() as session, session.begin():
        values = await registry.snapshot(session)
        original = values["WOS_SCHEDULER_MAX_ACTIVE_GLOBAL"]
        interval = values["WOS_QB_SYNC_INTERVAL_SECONDS"]
        if type(original) is not int or type(interval) is not int:
            raise RuntimeError("scheduler options are invalid")
        await registry.update(
            session,
            {"WOS_SCHEDULER_MAX_ACTIVE_GLOBAL": slots},
            actor_user_id=actor_user_id,
        )
    return registry, original, interval


async def _restore_slots(
    registry: PostgresOptionsRegistry,
    actor_user_id: uuid.UUID,
    original: int,
) -> None:
    async with session_factory() as session, session.begin():
        await registry.update(
            session,
            {"WOS_SCHEDULER_MAX_ACTIVE_GLOBAL": original},
            actor_user_id=actor_user_id,
        )


async def _release_lease(scheduler_id: str) -> None:
    async with session_factory() as session, session.begin():
        state = await session.scalar(
            select(SchedulerState).where(SchedulerState.id == 1).with_for_update()
        )
        if state is not None and state.lease_owner == scheduler_id:
            state.lease_owner = None
            state.lease_expires_at = None


async def _wait_for_leadership(runtime: SchedulerRuntime, timeout_seconds: float = 45) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = await runtime.run_once()
        if result.leader:
            return
        await asyncio.sleep(1)
    raise RuntimeError("pilot scheduler could not acquire the singleton lease")


async def _run_phase(
    runtime: SchedulerRuntime,
    *,
    seconds: int,
    interval_seconds: int,
    selected_users_by_torrent: Mapping[uuid.UUID, set[uuid.UUID]],
    collect_cycles: bool,
) -> tuple[float, list[float], set[uuid.UUID], int]:
    started = time.monotonic()
    deadline = started + seconds
    durations: list[float] = []
    serviced: set[uuid.UUID] = set()
    duplicate_count = 0
    last_progress = started

    while time.monotonic() < deadline:
        cycle_started = time.perf_counter()
        result = await runtime.run_once()
        elapsed = time.perf_counter() - cycle_started
        if not result.leader:
            raise RuntimeError("pilot scheduler lost the singleton lease")
        selected = result.selected_torrent_ids
        duplicate_count += len(selected) - len(set(selected))
        if collect_cycles:
            durations.append(elapsed)
            for torrent_id in selected:
                serviced.update(selected_users_by_torrent.get(torrent_id, ()))
        now = time.monotonic()
        if now - last_progress >= 60:
            print(
                f"load_elapsed_seconds={int(now - started)}",
                file=sys.stderr,
                flush=True,
            )
            last_progress = now
        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(float(interval_seconds), remaining))

    return time.monotonic() - started, durations, serviced, duplicate_count


async def _verify_campaign_after_run(
    campaign: str,
    specs: Sequence[DeploymentAccountSpec],
    *,
    data_root: Path,
    slots: int,
) -> tuple[int, int]:
    prefix = _prefix(campaign)
    unexpected = 0
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    ManagedTorrent.name,
                    ManagedTorrent.state,
                    ManagedTorrent.desired_active,
                    ManagedTorrent.scheduler_retry_at,
                ).where(ManagedTorrent.name.like(f"{prefix}%"))
            )
        ).all()
        requests = (
            await session.execute(
                select(ManagedTorrent.name, TorrentRequest.state)
                .join(TorrentRequest)
                .where(ManagedTorrent.name.like(f"{prefix}%"))
            )
        ).all()
        await session.rollback()

    if len(rows) != TOTAL_TORRENT_COUNT:
        unexpected += abs(TOTAL_TORRENT_COUNT - len(rows))
    active_count = 0
    for name, state, desired_active, retry_at in rows:
        try:
            index = int(name.rsplit("-", 1)[1])
        except (ValueError, IndexError):
            unexpected += 1
            continue
        expected_kind = _kind(index)
        expected_state = (
            ManagedTorrentState.READY if expected_kind == "ready" else ManagedTorrentState.PAUSED
        )
        if state != expected_state:
            unexpected += 1
        if desired_active:
            active_count += 1
        if expected_kind == "cooldown" and retry_at is None:
            unexpected += 1
    if active_count > slots:
        unexpected += active_count - slots

    for name, state in requests:
        try:
            index = int(name.rsplit("-", 1)[1])
        except (ValueError, IndexError):
            unexpected += 1
            continue
        expected = (
            TorrentRequestState.READY if _kind(index) == "ready" else TorrentRequestState.REQUESTED
        )
        if state != expected:
            unexpected += 1

    fixtures = _fixtures(campaign, len(specs))
    timeout = httpx.Timeout(connect=10, read=30, write=30, pool=10)
    corruption = 0
    async with httpx.AsyncClient(timeout=timeout) as client:
        gateways = _gateways(client, specs, data_root=data_root)
        by_route: dict[int, list[QBittorrentV2ManagedIdentity]] = {}
        for fixture in fixtures:
            by_route.setdefault(fixture.route_index, []).append(
                QBittorrentV2ManagedIdentity(fixture.info_hash, fixture.storage_key)
            )
        for route_index, identities in by_route.items():
            gateway = gateways[route_index]
            for offset in range(0, len(identities), 200):
                chunk = identities[offset : offset + 200]
                try:
                    snapshots = await gateway.inspect_managed_torrents(chunk)
                except Exception:
                    corruption += len(chunk)
                    continue
                if len(snapshots) != len(chunk):
                    corruption += abs(len(chunk) - len(snapshots))
    return corruption, unexpected


async def run_load(
    campaign: str,
    *,
    slots: int,
    warmup_seconds: int,
    measurement_seconds: int,
) -> dict[str, object]:
    if slots not in (1, 2):
        raise ValueError("slots must be 1 or 2")
    if warmup_seconds < 300 or measurement_seconds < 1800:
        raise ValueError("pilot load requires at least 300s warmup and 1800s measurement")
    settings, specs = _safe_runtime()
    campaign = _campaign(campaign)
    async with session_factory() as session:
        counts = await _campaign_counts(session, campaign)
        await session.rollback()
    if counts["users"] != ACCOUNT_COUNT or counts["torrents"] != TOTAL_TORRENT_COUNT:
        raise RuntimeError("campaign is not fully prepared")

    await _reject_non_pilot_scheduler_candidates(campaign)
    mapping, eligible_users, actor = await _campaign_maps(campaign)
    registry, original_slots, interval_seconds = await _set_slots(actor, slots)
    scheduler_id = f"pilot:{campaign}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    timeout = httpx.Timeout(
        connect=settings.integration_connect_timeout_seconds,
        read=settings.integration_read_timeout_seconds,
        write=settings.integration_read_timeout_seconds,
        pool=settings.integration_connect_timeout_seconds,
    )

    started = time.monotonic()
    warmup_elapsed = 0.0
    measurement_elapsed = 0.0
    cycle_durations: list[float] = []
    serviced_users: set[uuid.UUID] = set()
    duplicate_count = 0
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            router = build_deployment_account_router(
                settings.integration_accounts_json,
                client,
                session_factory,
                allowed_tracker_hosts=settings.c411_tracker_hosts,
                data_root=settings.qbittorrent_data_root,
                max_total_size=MAX_MANAGED_TORRENT_BYTES,
            )
            runtime = SchedulerRuntime(session_factory, router, scheduler_id=scheduler_id)
            await _wait_for_leadership(runtime)
            warmup_elapsed, _, _, warmup_duplicates = await _run_phase(
                runtime,
                seconds=warmup_seconds,
                interval_seconds=interval_seconds,
                selected_users_by_torrent=mapping,
                collect_cycles=False,
            )
            (
                measurement_elapsed,
                cycle_durations,
                serviced_users,
                measurement_duplicates,
            ) = await _run_phase(
                runtime,
                seconds=measurement_seconds,
                interval_seconds=interval_seconds,
                selected_users_by_torrent=mapping,
                collect_cycles=True,
            )
            duplicate_count = warmup_duplicates + measurement_duplicates
    finally:
        try:
            await _restore_slots(registry, actor, original_slots)
        finally:
            await _release_lease(scheduler_id)

    corruption_count, unexpected_transition_count = await _verify_campaign_after_run(
        campaign,
        specs,
        data_root=Path(settings.qbittorrent_data_root),
        slots=slots,
    )
    famine_count = len(eligible_users - serviced_users)
    p95 = _percentile(cycle_durations, 0.95)
    total_duration = time.monotonic() - started
    passed = (
        famine_count == 0
        and duplicate_count == 0
        and corruption_count == 0
        and unexpected_transition_count == 0
        and p95 < interval_seconds
        and warmup_elapsed >= 300
        and measurement_elapsed >= 1800
        and total_duration >= 2100
    )
    return {
        "schema": SCHEMA,
        "phase": "load",
        "campaign": campaign,
        "status": "passed" if passed else "failed",
        "slots": slots,
        "warmup_seconds": round(warmup_elapsed, 3),
        "measurement_seconds": round(measurement_elapsed, 3),
        "duration_seconds": round(total_duration, 3),
        "famine_count": famine_count,
        "duplicate_count": duplicate_count,
        "corruption_count": corruption_count,
        "unexpected_transition_count": unexpected_transition_count,
        "scheduler_cycle_p95_seconds": round(p95, 6),
        "scheduler_interval_seconds": interval_seconds,
        "measurement_cycle_count": len(cycle_durations),
        "serviced_account_count": len(serviced_users),
        "account_count": ACCOUNT_COUNT,
        "backlog_count": ELIGIBLE_TORRENT_COUNT,
        "control_window_max": 200,
        "secrets_or_business_identifiers_in_report": False,
    }


async def status(campaign: str) -> dict[str, object]:
    _, specs = _safe_runtime()
    campaign = _campaign(campaign)
    async with session_factory() as session:
        counts = await _campaign_counts(session, campaign)
        options = await PostgresOptionsRegistry().snapshot(session)
        await session.rollback()
    return {
        "schema": SCHEMA,
        "phase": "status",
        "campaign": campaign,
        "account_count": counts["users"],
        "managed_torrent_count": counts["torrents"],
        "request_count": counts["requests"],
        "scheduler_max_active_global": options["WOS_SCHEDULER_MAX_ACTIVE_GLOBAL"],
        "scheduler_interval_seconds": options["WOS_QB_SYNC_INTERVAL_SECONDS"],
        "deployment_route_count": len(specs),
        "secrets_or_business_identifiers_in_report": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    for name in ("prepare", "status"):
        command = commands.add_parser(name)
        command.add_argument("--campaign", required=True)

    run = commands.add_parser("run")
    run.add_argument("--campaign", required=True)
    run.add_argument("--slots", type=int, choices=(1, 2), required=True)
    run.add_argument("--warmup-seconds", type=int, default=300)
    run.add_argument("--measurement-seconds", type=int, default=1800)
    return parser


async def _main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            report = await prepare(args.campaign)
        elif args.command == "status":
            report = await status(args.campaign)
        else:
            report = await run_load(
                args.campaign,
                slots=args.slots,
                warmup_seconds=args.warmup_seconds,
                measurement_seconds=args.measurement_seconds,
            )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 2 if report.get("status") == "failed" else 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
