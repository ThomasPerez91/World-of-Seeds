import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from httpx import AsyncClient
from starlette.types import Message, Scope

from app.http_downloads import OpenedDownload
from app.main import app
from app.models import DownloadLease
from app.torrents.downloads import (
    DownloadLeaseManager,
    DownloadRateLimiter,
    ManagedDownloadError,
    ManagedDownloadStreamingResponse,
)
from app.torrents.traffic import DownloadTrafficScheduler, DownloadWaitingLimit


@pytest.mark.asyncio
async def test_metrics_are_cumulative_bounded_and_measure_waits_and_freshness() -> None:
    now = 100.0
    scheduler = DownloadTrafficScheduler(clock=lambda: now)
    active = [uuid.uuid4() for _ in range(5)]
    for lease in active:
        await scheduler.register(lease, uuid.uuid4(), 1000)
    waiting_user = uuid.uuid4()
    waiting = [uuid.uuid4() for _ in range(2)]
    for lease in waiting:
        await scheduler.register(lease, waiting_user, 10_000)
    with pytest.raises(DownloadWaitingLimit):
        await scheduler.register(uuid.uuid4(), waiting_user, 10_000)
    now += 10
    await scheduler.report(waiting[0], 1024)
    await scheduler.observe_upload(124_000_000, sample_age_seconds=5)
    output = "\n".join(await scheduler.render_metrics())
    assert "wos_http_download_waiting_oldest_age_seconds 10.000000" in output
    assert 'wos_http_download_bytes_total{lane="waiting"} 1024' in output
    assert "wos_http_download_telemetry_age_seconds 5.000000" in output
    assert "wos_http_download_telemetry_fresh 1.000000" in output
    assert "wos_http_download_waiting_rejected_total 1" in output
    await scheduler.unregister(active[0], outcome="completed")
    await scheduler.unregister(waiting[0], outcome="interrupted")
    now += 50
    await scheduler.observe_upload(None)
    output = "\n".join(await scheduler.render_metrics())
    assert "wos_http_download_telemetry_fresh 0.000000" in output
    assert 'wos_http_download_ended_total{outcome="completed"} 1' in output
    assert 'wos_http_download_ended_total{outcome="interrupted"} 1' in output
    assert "wos_http_download_fast_grant_seconds_count 7" in output
    assert str(waiting_user) not in output
    assert str(waiting[0]) not in output


@pytest.mark.asyncio
async def test_scrape_exposes_live_traffic_without_caching_or_identifiers(
    client: AsyncClient,
) -> None:
    scheduler = app.state.download_traffic_scheduler
    lease, user = uuid.uuid4(), uuid.uuid4()
    await scheduler.register(lease, user, 100)
    response = await client.get("/api/v2/metrics")
    assert response.status_code == 200
    assert "wos_http_download_fast_streams 1.000000" in response.text
    assert str(user) not in response.text
    await scheduler.unregister(lease)
    response = await client.get("/api/v2/metrics")
    assert "wos_http_download_fast_streams 0.000000" in response.text


class RecordingLeases:
    heartbeat_seconds = 0.01

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.renewals = 0
        self.released = 0

    async def keep_alive(self, lease_id: uuid.UUID) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            self.renewals += 1
            if self.fail and self.renewals >= 3:
                raise ManagedDownloadError("lease lost")

    async def release(self, lease_id: uuid.UUID) -> None:
        self.released += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["completed", "interrupted", "error"])
async def test_heartbeat_runs_during_send_backpressure_and_cleanup_is_complete(
    tmp_path: Path, mode: str
) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"x" * 2048)
    opened = OpenedDownload(
        os.open(path, os.O_RDONLY),
        "payload.bin",
        2048,
        datetime.now(UTC),
        "application/octet-stream",
        '"etag"',
    )
    leases = RecordingLeases(fail=mode == "error")
    scheduler = DownloadTrafficScheduler()
    lease_id, user_id = uuid.uuid4(), uuid.uuid4()
    await scheduler.register(lease_id, user_id, 2048)
    response = ManagedDownloadStreamingResponse(
        opened,
        start=0,
        length=2048,
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
    started, allow_send = asyncio.Event(), asyncio.Event()

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            started.set()
            await allow_send.wait()

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    scope = cast(Scope, {"type": "http", "asgi": {"spec_version": "2.4"}})
    task = asyncio.create_task(response(scope, receive, send))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.sleep(0.055)
        assert leases.renewals >= 3
        assert "wos_http_download_first_byte_seconds_count 0" in "\n".join(
            await scheduler.render_metrics()
        )
        if mode == "error":
            with pytest.raises(ExceptionGroup):
                await task
        elif mode == "interrupted":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            allow_send.set()
            await task
        assert await scheduler.snapshot() == (0, 0)
        assert leases.released == 1
        output = "\n".join(await scheduler.render_metrics())
        outcome = mode
        assert f'wos_http_download_ended_total{{outcome="{outcome}"}} 1' in output
        assert (
            f'wos_http_download_bytes_total{{lane="fast"}} {2048 if mode == "completed" else 0}'
            in output
        )
        with pytest.raises(OSError):
            os.fstat(opened.file_descriptor)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
