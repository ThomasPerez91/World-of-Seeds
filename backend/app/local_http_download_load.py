"""Isolated loopback campaign on real READY routes; SQLite, no qB traffic."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import resource
import socket
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth.security import DUMMY_PASSWORD_HASH
from app.auth.service import issue_session
from app.benchmark_http_downloads import Manifest, Target, campaign
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.main import create_app
from app.models import (
    Base,
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


@asynccontextmanager
async def local_fixture(
    root: Path, clients: int, *, serve: bool = True
) -> AsyncIterator[tuple[Manifest, FastAPI]]:
    settings = Settings(data_root=root / "data", runtime_profile="v2", allowed_hosts=["127.0.0.1"])
    settings.data_root.mkdir()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{root / 'load.sqlite'}", connect_args={"timeout": 30}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.run_sync(Base.metadata.create_all)
    application = create_app(settings)

    async def db_dependency() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    application.dependency_overrides[get_db_session] = db_dependency
    application.dependency_overrides[get_settings] = lambda: settings
    targets: list[Target] = []
    now = datetime.now(UTC)
    async with factory() as session:
        await PostgresOptionsRegistry().initialize(session)
        files: list[tuple[ManagedTorrent, TorrentFile, str]] = []
        for index, size in enumerate((2 * 1024 * 1024, 128 * 1024 * 1024)):
            torrent = ManagedTorrent(
                info_hash=str(index + 1) * 40,
                name="local load fixture",
                total_size=size,
                state=ManagedTorrentState.READY,
                progress=1,
                ready_at=now,
                retention_expires_at=now + timedelta(days=1),
                manifest_version=1,
                manifest_checksum=str(index + 1) * 64,
                manifest_file_count=1,
                manifest_total_size=size,
            )
            file = TorrentFile(
                managed_torrent=torrent, file_index=0, relative_path="payload.bin", size=size
            )
            session.add_all([torrent, file])
            await session.flush()
            store = SharedContentStore(settings.data_root)
            store.prepare(torrent.storage_key)
            path = settings.data_root / "content" / torrent.storage_key.hex / "payload.bin"
            digest = hashlib.sha256()
            block = bytes([index + 1]) * (1024 * 1024)
            with path.open("wb") as output:
                for _ in range(size // len(block)):
                    output.write(block)
                    digest.update(block)
            files.append((torrent, file, digest.hexdigest()))
        for index in range(clients):
            large = index < 5 or index % 10 == 0
            torrent, file, checksum = files[int(large)]
            user = User(username=f"load-{index:03}", password_hash=DUMMY_PASSWORD_HASH)
            request = TorrentRequest(
                user=user,
                managed_torrent=torrent,
                state=TorrentRequestState.READY,
                unsubscribe_at=now + timedelta(days=1),
            )
            session.add_all([user, request])
            await session.flush()
            tokens = issue_session(session, user=user, settings=settings)
            targets.append(
                Target(
                    path=f"/api/v2/torrents/{request.id}/files/{file.id}/download",
                    session_token=SecretStr(tokens.session_token),
                    expected_bytes=file.size,
                    expected_sha256=checksum,
                    read_bytes_per_second=500_000 if large and index != 30 else 0,
                    cancel_after_bytes=64 * 1024 if index % 11 == 6 else 0,
                    resume_after_cancel=index % 11 == 6,
                )
            )
        await session.commit()
    if not serve:
        try:
            yield Manifest(base_url="http://127.0.0.1", targets=targets), application
        finally:
            await engine.dispose()
        return
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(application, lifespan="off", log_level="critical", access_log=False)
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:  # noqa: ASYNC110 -- Uvicorn exposes a flag, not an event.
                if task.done():
                    await task
                    raise RuntimeError("local server did not start")
                await asyncio.sleep(0.01)
        yield Manifest(base_url=f"http://127.0.0.1:{port}", targets=targets), application
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        sock.close()
        await engine.dispose()


def loopback_bytes() -> int:
    for line in Path("/proc/net/dev").read_text().splitlines():
        if line.strip().startswith("lo:"):
            return int(line.split(":", 1)[1].split()[8])
    raise RuntimeError("loopback counters unavailable")


async def run_local(*, clients: int, seconds: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="wos-http-load-") as directory:
        async with local_fixture(Path(directory), clients) as (manifest, application):
            scheduler = application.state.download_traffic_scheduler
            observations: list[tuple[int, int]] = []
            started = asyncio.get_running_loop().time()
            before_usage = resource.getrusage(resource.RUSAGE_SELF)

            async def telemetry() -> None:
                previous_bytes, previous_time = loopback_bytes(), started
                while True:
                    observations.append(await scheduler.snapshot())
                    await asyncio.sleep(1)
                    now = asyncio.get_running_loop().time()
                    if now - previous_time >= 15:
                        current_bytes = loopback_bytes()
                        await scheduler.observe_upload(
                            (current_bytes - previous_bytes) / (now - previous_time)
                        )
                        previous_bytes, previous_time = current_bytes, now

            monitor = asyncio.create_task(telemetry())
            try:
                report = await campaign(
                    manifest,
                    seconds=seconds,
                    warmup_clients=min(5, clients - 1),
                    warmup_seconds=min(1, seconds),
                )
                # Wait for ASGI disconnect cleanup before checking slots and leases.
                async with asyncio.timeout(10):
                    while await scheduler.snapshot() != (0, 0):  # noqa: ASYNC110
                        await asyncio.sleep(0.05)
                metrics = await scheduler.render_metrics()
                db_override = application.dependency_overrides[get_db_session]
                async for session in db_override():
                    remaining_leases = await session.scalar(
                        select(func.count()).select_from(DownloadLease)
                    )
                after_usage = resource.getrusage(resource.RUSAGE_SELF)
                report.update(
                    {
                        "environment": "Loopback, SQLite WAL, real READY HTTP routes",
                        "qbittorrent_active": False,
                        "network_observation": "Loopback TX every 15s; no Rise2 Prometheus",
                        "peak_active_streams": max((a + b for a, b in observations), default=0),
                        "peak_waiting_streams": max((b for _, b in observations), default=0),
                        "remaining_leases": remaining_leases,
                        "final_slots": list(await scheduler.snapshot()),
                        "process_cpu_seconds": after_usage.ru_utime
                        + after_usage.ru_stime
                        - before_usage.ru_utime
                        - before_usage.ru_stime,
                        "process_max_rss_kib": after_usage.ru_maxrss,
                        "process_input_blocks": after_usage.ru_inblock - before_usage.ru_inblock,
                        "process_output_blocks": after_usage.ru_oublock - before_usage.ru_oublock,
                        "allocated_fixture_bytes": 130 * 1024 * 1024,
                        "http_metrics": metrics,
                    }
                )
                return report
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int, default=50)
    parser.add_argument("--seconds", type=int, default=45)
    args = parser.parse_args()
    if not 5 <= args.clients <= 100 or not 1 <= args.seconds <= 300:
        parser.error("clients must be 5..100; duration 1..300 seconds")
    print(json.dumps(asyncio.run(run_local(clients=args.clients, seconds=args.seconds)), indent=2))


if __name__ == "__main__":
    main()
