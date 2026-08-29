"""Disposable, development-only V2 load smoke for the complete local profile."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import statistics
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import websockets
from sqlalchemy import delete, func, select, text

from app.auth.service import issue_session
from app.core.config import get_settings
from app.core.database import engine, session_factory
from app.integrations.account_routing import parse_deployment_account_specs
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2Gateway,
    QBittorrentV2ManagedIdentity,
    QBittorrentV2TransientError,
)
from app.models import (
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.options import PostgresOptionsRegistry
from app.storage import SharedContentStore

ACCOUNT_COUNT = 100
SCALES = (1, 10, 25, 50, 100)
PREFIX = "load-v2-32-"
CONTENT = b"World of Seeds V2 bounded load smoke\n"
STORAGE_KEY = uuid.UUID("32323232-3232-4232-8232-323232323232")


@dataclass(frozen=True, slots=True)
class LoadIdentity:
    session_token: str
    request_id: str
    file_id: str


def _percentile(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * percentile))
    return ordered[index]


def _torrent_fixture() -> tuple[bytes, str]:
    name = b"load.txt"
    info = (
        b"d6:lengthi"
        + str(len(CONTENT)).encode()
        + b"e4:name"
        + str(len(name)).encode()
        + b":"
        + name
        + b"12:piece lengthi16384e6:pieces20:"
        + hashlib.sha1(CONTENT).digest()
        + b"e"
    )
    metainfo = b"d8:announce30:https://c411.org/announce/test4:info" + info + b"e"
    return metainfo, hashlib.sha1(info).hexdigest()


async def _prepare_qbittorrent_fixture(
    metainfo: bytes,
    info_hash: str,
) -> tuple[uuid.UUID, uuid.UUID]:
    settings = get_settings()
    if settings.integration_accounts_json is None:
        raise RuntimeError("V2 load smoke requires the disposable integration registry")
    specs = parse_deployment_account_specs(settings.integration_accounts_json)
    spec = specs[int(info_hash, 16) % len(specs)]
    async with httpx.AsyncClient(timeout=20) as client:
        gateway = QBittorrentV2Gateway(
            client,
            spec.qbittorrent_url,
            spec.qbittorrent_username,
            spec.qbittorrent_password.get_secret_value(),
            data_root=Path(settings.qbittorrent_data_root),
        )
        await gateway.add_managed_torrent(
            metainfo,
            expected_info_hash=info_hash,
            storage_key=STORAGE_KEY,
        )
        identity = QBittorrentV2ManagedIdentity(info_hash, STORAGE_KEY)
        for _ in range(100):
            try:
                snapshots = await gateway.inspect_managed_torrents((identity,))
            except QBittorrentV2TransientError:
                snapshots = ()
            if len(snapshots) == 1 and snapshots[0].progress >= 1:
                return spec.tracker_account_ref, spec.qbittorrent_account_ref
            await asyncio.sleep(0.1)
    raise RuntimeError("qBittorrent did not verify the bounded load fixture")


async def _seed() -> list[LoadIdentity]:
    settings = get_settings()
    if settings.environment != "development" or os.environ.get("WOS_RUNTIME_PROFILE") != "v2":
        raise RuntimeError("V2 load smoke is restricted to the disposable development profile")
    metainfo, info_hash = _torrent_fixture()

    async with session_factory() as session, session.begin():
        previous_users = select(User.id).where(User.username.like(f"{PREFIX}%"))
        await session.execute(delete(User).where(User.id.in_(previous_users)))
        await session.execute(delete(ManagedTorrent).where(ManagedTorrent.name == PREFIX))
        await PostgresOptionsRegistry().initialize(session)

        torrent = ManagedTorrent(
            info_hash=info_hash,
            name=PREFIX,
            total_size=len(CONTENT),
            storage_key=STORAGE_KEY,
            state=ManagedTorrentState.PENDING,
            progress=0.0,
            manifest_version=1,
            manifest_checksum="e" * 64,
            manifest_file_count=1,
            manifest_total_size=len(CONTENT),
        )
        session.add(torrent)
        await session.flush()
        torrent_file = TorrentFile(
            managed_torrent_id=torrent.id,
            file_index=0,
            relative_path="load.txt",
            size=len(CONTENT),
        )
        session.add(torrent_file)
        await session.flush()

        identities: list[LoadIdentity] = []
        for index in range(ACCOUNT_COUNT):
            user = User(
                username=f"{PREFIX}{index:03d}",
                password_hash="development-load-smoke-disabled-password",
            )
            session.add(user)
            await session.flush()
            tokens = issue_session(session, user=user, settings=settings)
            request = TorrentRequest(
                user_id=user.id,
                managed_torrent_id=torrent.id,
                state=TorrentRequestState.READY,
                ready_at=datetime.now(UTC),
            )
            session.add(request)
            await session.flush()
            identities.append(
                LoadIdentity(tokens.session_token, str(request.id), str(torrent_file.id))
            )

        storage_key = torrent.storage_key

    SharedContentStore(Path(settings.data_root)).prepare(storage_key)
    target = Path(settings.data_root) / "content" / storage_key.hex / "load.txt"
    target.write_bytes(CONTENT)
    tracker_ref, qbittorrent_ref = await _prepare_qbittorrent_fixture(metainfo, info_hash)
    async with session_factory() as session, session.begin():
        verified_torrent = await session.scalar(
            select(ManagedTorrent)
            .where(ManagedTorrent.storage_key == STORAGE_KEY)
            .with_for_update()
        )
        if verified_torrent is None or verified_torrent.state is not ManagedTorrentState.PENDING:
            raise RuntimeError("bounded load fixture changed before qBittorrent verification")
        verified_torrent.tracker_account_ref = tracker_ref
        verified_torrent.qbittorrent_account_ref = qbittorrent_ref
        verified_torrent.state = ManagedTorrentState.READY
        verified_torrent.progress = 1.0
    return identities


async def _request_batch(
    client: httpx.AsyncClient,
    identities: list[LoadIdentity],
    cookie_name: str,
) -> tuple[list[float], int]:
    async def exercise(identity: LoadIdentity) -> tuple[float, int, int, int, int, bool]:
        started = time.perf_counter()
        headers = {"Cookie": f"{cookie_name}={identity.session_token}"}
        listing = await client.get("/api/v2/torrents", params={"limit": 1}, headers=headers)
        manifest = await client.get(
            f"/api/v2/torrents/{identity.request_id}/download-manifest",
            params={"limit": 1},
            headers=headers,
        )
        retries = 0
        while True:
            download = await client.get(
                f"/api/v2/torrents/{identity.request_id}/files/{identity.file_id}/download",
                headers={**headers, "Range": "bytes=0-0"},
            )
            if download.status_code != 429 or retries >= 5:
                break
            retries += 1
            await asyncio.sleep(0.05 * retries)
        return (
            time.perf_counter() - started,
            retries,
            listing.status_code,
            manifest.status_code,
            download.status_code,
            download.content == CONTENT[:1],
        )

    measured = await asyncio.gather(*(exercise(identity) for identity in identities))
    failures: dict[str, dict[int, int]] = {}
    expectations = (("listing", 2, 200), ("manifest", 3, 200), ("download", 4, 206))
    for endpoint, position, expected_status in expectations:
        statuses: dict[int, int] = {}
        for result in measured:
            status_code = int(result[position])
            if status_code != expected_status:
                statuses[status_code] = statuses.get(status_code, 0) + 1
        if statuses:
            failures[endpoint] = dict(sorted(statuses.items()))
    content_failures = sum(not result[5] for result in measured)
    if content_failures:
        failures["download_content"] = {0: content_failures}
    if failures:
        snapshot = await _fixture_snapshot()
        raise RuntimeError(
            "concurrent authenticated load failed: "
            f"scale={len(identities)}, failures={failures}, "
            f"fixture={snapshot}"
        )
    return (
        [elapsed for elapsed, _, _, _, _, _ in measured],
        sum(retries for _, retries, _, _, _, _ in measured),
    )


async def _fixture_snapshot() -> dict[str, object]:
    """Return only aggregate fixture state for bounded CI failure diagnostics."""
    async with session_factory() as session:
        managed_state = await session.scalar(
            select(ManagedTorrent.state).where(ManagedTorrent.name == PREFIX)
        )
        request_states = (
            await session.execute(
                select(TorrentRequest.state, func.count())
                .join(ManagedTorrent)
                .where(ManagedTorrent.name == PREFIX)
                .group_by(TorrentRequest.state)
            )
        ).all()
        file_count = await session.scalar(
            select(func.count())
            .select_from(TorrentFile)
            .join(ManagedTorrent)
            .where(ManagedTorrent.name == PREFIX)
        )
        await session.rollback()
    return {
        "managed_state": managed_state.value if managed_state is not None else "missing",
        "request_states": {
            state.value: int(count)
            for state, count in sorted(request_states, key=lambda row: row[0])
        },
        "file_count": int(file_count or 0),
    }


async def _database_snapshot() -> dict[str, int]:
    async with session_factory() as session:
        activity = (
            await session.execute(
                text(
                    "SELECT count(*) FILTER (WHERE state = 'idle in transaction'), count(*) "
                    "FROM pg_stat_activity WHERE datname = current_database()"
                )
            )
        ).one()
        leases = await session.scalar(select(func.count()).select_from(DownloadLease))
        await session.rollback()
    return {
        "idle_in_transaction": int(activity[0]),
        "database_connections": int(activity[1]),
        "active_download_leases": int(leases or 0),
    }


async def _websocket_batch(
    identities: list[LoadIdentity],
    cookie_name: str,
) -> None:
    uri = "ws://127.0.0.1:8000/api/v2/torrents/events"

    async def connect(identity: LoadIdentity) -> websockets.ClientConnection:
        return await websockets.connect(
            uri,
            additional_headers={"Cookie": f"{cookie_name}={identity.session_token}"},
            proxy=None,
            open_timeout=20,
            close_timeout=5,
        )

    sockets = await asyncio.gather(*(connect(identity) for identity in identities))
    try:
        snapshot = await _database_snapshot()
        if snapshot["idle_in_transaction"] != 0:
            raise RuntimeError("WebSocket idle time retained a PostgreSQL transaction")
    finally:
        await asyncio.gather(*(socket.close() for socket in sockets))


async def run() -> dict[str, object]:
    identities = await _seed()
    settings = get_settings()
    results: list[dict[str, object]] = []
    limits = httpx.Limits(max_connections=120, max_keepalive_connections=20)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        timeout=30,
        limits=limits,
    ) as client:
        for scale in SCALES:
            elapsed, retries = await _request_batch(
                client,
                identities[:scale],
                settings.session_cookie_name,
            )
            results.append(
                {
                    "accounts": scale,
                    "requests": scale * 3,
                    "range_contention_retries": retries,
                    "median_ms": round(statistics.median(elapsed) * 1000, 1),
                    "p95_ms": round(_percentile(elapsed, 0.95) * 1000, 1),
                }
            )

    # The 100 sockets represent 25 accounts with four tabs each. Reopen 25 once to
    # exercise the reconnect path without retaining a SQL session between attempts.
    multi_tab = [identities[index % 25] for index in range(ACCOUNT_COUNT)]
    await _websocket_batch(multi_tab, settings.session_cookie_name)
    await _websocket_batch(identities[:25], settings.session_cookie_name)
    database = await _database_snapshot()
    if database["active_download_leases"] != 0:
        raise RuntimeError("completed downloads retained a database lease")
    if database["idle_in_transaction"] != 0:
        raise RuntimeError("load smoke retained an idle PostgreSQL transaction")
    if database["database_connections"] > 40:
        raise RuntimeError("the single-process profile exceeded its bounded connection budget")

    return {
        "profile": "development-v2-single-process",
        "api_load": results,
        "websockets": {"connections": 100, "accounts": 25, "tabs_per_account": 4},
        "reconnections": 25,
        "database": database,
        "secrets_or_business_identifiers_in_report": False,
    }


async def _main() -> None:
    try:
        print(json.dumps(await run(), indent=2, sort_keys=True))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
