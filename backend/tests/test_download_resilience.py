import asyncio
import os
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Message, Scope

from app import main as app_main
from app.http_downloads import OpenedDownload
from app.models import DownloadLease
from app.torrents import downloads
from app.torrents.downloads import (
    DownloadLeaseManager,
    DownloadRateLimiter,
    ManagedDownloadError,
    ManagedDownloadStreamingResponse,
    ManagedFileDownloader,
    _zip_timestamp,
)
from app.torrents.traffic import DownloadTrafficScheduler


class _Stop(Exception):
    pass


class _ScriptedLeases(DownloadLeaseManager):
    """Lease manager whose independent renewals follow a script (None means success)."""

    def __init__(self, script: list[BaseException | None], *, lease_seconds: int = 120) -> None:
        self.now = datetime(2026, 10, 24, 12, tzinfo=UTC)
        self.waits: list[float] = []
        self.script = list(script)
        super().__init__(
            cast(AsyncSession, SimpleNamespace(bind=None)),
            lease_seconds=lease_seconds,
            clock=lambda: self.now,
            heartbeat_wait=self._wait,
        )

    async def _wait(self, seconds: float) -> None:
        if not self.script:
            raise _Stop
        self.waits.append(seconds)
        self.now += timedelta(seconds=seconds)

    async def renew_independent(self, lease_id: uuid.UUID) -> None:
        outcome = self.script.pop(0)
        if outcome is not None:
            raise outcome


def _database_blip() -> OperationalError:
    return OperationalError("UPDATE download_leases", {}, Exception("connection reset"))


@pytest.mark.asyncio
async def test_keep_alive_retries_a_transient_database_error_and_resumes_the_cadence() -> None:
    leases = _ScriptedLeases([None, _database_blip(), TimeoutError(), None, None])

    with pytest.raises(_Stop):
        await leases.keep_alive(uuid.uuid4())

    # Normal heartbeat, quick retries while the lease is still valid, then the cadence resumes.
    assert leases.waits == [40.0, 40.0, 10.0, 10.0, 40.0]


@pytest.mark.asyncio
async def test_keep_alive_gives_up_only_when_the_lease_is_about_to_expire() -> None:
    attempts = 20
    leases = _ScriptedLeases([_database_blip() for _ in range(attempts)])

    with pytest.raises(OperationalError):
        await leases.keep_alive(uuid.uuid4())

    elapsed = (leases.now - datetime(2026, 10, 24, 12, tzinfo=UTC)).total_seconds()
    assert 108 <= elapsed < 120
    assert len(leases.script) > 0


@pytest.mark.asyncio
async def test_keep_alive_stops_at_once_when_the_lease_is_really_lost() -> None:
    leases = _ScriptedLeases([ManagedDownloadError("download lease was lost"), None])

    with pytest.raises(ManagedDownloadError):
        await leases.keep_alive(uuid.uuid4())

    assert leases.waits == [40.0]


class _RecordingLeases:
    heartbeat_seconds = 0.01

    def __init__(self) -> None:
        self.released = 0

    async def keep_alive(self, lease_id: uuid.UUID) -> None:
        await asyncio.Event().wait()

    async def release(self, lease_id: uuid.UUID) -> None:
        self.released += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_stalled_client_frees_the_lane_and_the_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spec_version: str
) -> None:
    monkeypatch.setattr(downloads, "SEND_STALL_TIMEOUT_SECONDS", 0.05)
    path = tmp_path / "payload.bin"
    path.write_bytes(b"x" * 4096)
    opened = OpenedDownload(
        os.open(path, os.O_RDONLY),
        "payload.bin",
        4096,
        datetime.now(UTC),
        "application/octet-stream",
        '"etag"',
    )
    leases = _RecordingLeases()
    scheduler = DownloadTrafficScheduler()
    lease_id, user_id = uuid.uuid4(), uuid.uuid4()
    await scheduler.register(lease_id, user_id, 4096)
    response = ManagedDownloadStreamingResponse(
        opened,
        start=0,
        length=4096,
        status_code=200,
        headers={},
        chunk_size=1024,
        user_id=user_id,
        lease=cast(DownloadLease, SimpleNamespace(id=lease_id)),
        leases=cast(DownloadLeaseManager, leases),
        limiter=DownloadRateLimiter(),
        traffic=scheduler,
        per_user_bytes_per_second=0,
        global_bytes_per_second=0,
    )
    never = asyncio.Event()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            await never.wait()

    async def receive() -> Message:
        await never.wait()
        return {"type": "http.disconnect"}

    scope = cast(Scope, {"type": "http", "asgi": {"spec_version": spec_version}})

    # The response ends by itself instead of waiting for a client that never reads again.
    await asyncio.wait_for(response(scope, receive, send), 2)

    assert await scheduler.snapshot() == (0, 0)
    assert leases.released == 1
    output = "\n".join(await scheduler.render_metrics())
    assert 'wos_http_download_ended_total{outcome="interrupted"} 1' in output
    with pytest.raises(OSError):
        os.fstat(opened.file_descriptor)


def test_zip_timestamps_are_clamped_to_the_range_the_format_can_store() -> None:
    assert _zip_timestamp(datetime(1970, 1, 1, tzinfo=UTC)) == (1980, 1, 1, 0, 0, 0)
    assert _zip_timestamp(datetime(2200, 5, 6, 7, 8, 9, tzinfo=UTC)) == (
        2107,
        12,
        31,
        23,
        59,
        58,
    )
    assert _zip_timestamp(datetime(2026, 10, 24, 12, 30, 20, tzinfo=UTC)) == (
        2026,
        10,
        24,
        12,
        30,
        20,
    )
    # The clamped value is accepted by the ZIP writer.
    zipfile.ZipInfo("a.txt", _zip_timestamp(datetime(1970, 1, 1, tzinfo=UTC)))


def test_a_file_with_an_absurd_modification_time_stays_downloadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.storage import SharedContentStore

    storage_key = uuid.uuid4()
    folder = tmp_path / "content" / storage_key.hex
    folder.mkdir(parents=True)
    (folder / "film.bin").write_bytes(b"abcde")
    real_fstat = os.fstat

    def fstat(descriptor: int) -> os.stat_result:
        result = real_fstat(descriptor)
        values = list(result)
        values[8] = 10**13  # st_mtime far beyond year 9999
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", fstat)
    downloader = ManagedFileDownloader(SharedContentStore(tmp_path))
    opened = downloader.open(
        storage_key,
        "film.bin",
        expected_size=5,
        manifest_checksum="c" * 64,
        manifest_version=1,
        file_index=0,
    )
    try:
        assert opened.modified_at.year == 1970
    finally:
        opened.close()


@pytest.mark.asyncio
async def test_upload_monitor_survives_an_unexpected_iteration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def snapshot(*_: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("unexpected collector bug")
        return SimpleNamespace(status="unavailable", upload=None)

    sleeps = 0

    async def sleep(_: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            raise _Stop

    application = cast(
        FastAPI,
        SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(prometheus_url="http://prometheus.test"),
                network_throughput_collector=SimpleNamespace(snapshot=snapshot),
                download_traffic_scheduler=DownloadTrafficScheduler(),
            )
        ),
    )
    monkeypatch.setattr(app_main, "asyncio", SimpleNamespace(sleep=sleep))

    with pytest.raises(_Stop):
        await app_main.monitor_http_upload(application)

    assert calls == 2
