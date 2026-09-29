import asyncio
import math
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.integrations.prometheus_network import (
    NetworkDirection,
    NetworkSample,
    NetworkThroughputSnapshot,
    PrometheusNetworkClient,
)
from app.main import monitor_http_upload
from app.torrents.downloads import DownloadRateLimiter
from app.torrents.traffic import DownloadTrafficScheduler, DownloadWaitingLimit


@pytest.mark.asyncio
async def test_fifty_users_get_five_fast_and_forty_five_slow_streams() -> None:
    scheduler = DownloadTrafficScheduler()
    transfers = [(uuid.uuid4(), uuid.uuid4()) for _ in range(50)]
    for lease_id, user_id in transfers:
        await scheduler.register(lease_id, user_id, 20_000_000_000)

    assert await scheduler.snapshot() == (5, 45)
    own = await scheduler.status(transfers[-1][1])
    assert own["my_fast_streams"] == 0
    assert own["my_waiting_streams"] == 1
    assert own["waiting_streams"] == 45
    assert await scheduler.next_chunk(transfers[0][0], 1_048_576) == (1_048_576, 0)
    assert await scheduler.next_chunk(transfers[-1][0], 1_048_576) == (1024, 1024)

    await scheduler.unregister(transfers[0][0])
    assert await scheduler.snapshot() == (5, 44)
    assert await scheduler.next_chunk(transfers[5][0], 1_048_576) == (1_048_576, 0)


@pytest.mark.asyncio
async def test_low_uplink_usage_promotes_all_fifty_without_reserving_fixed_shares() -> None:
    scheduler = DownloadTrafficScheduler(uplink_bytes_per_second=125_000_000)
    leases = [uuid.uuid4() for _ in range(50)]
    for lease in leases:
        await scheduler.register(lease, uuid.uuid4(), 20_000_000_000)
    assert await scheduler.snapshot() == (5, 45)
    for _ in range(12):
        await scheduler.observe_upload(5 * 500_000)
    assert await scheduler.snapshot() == (50, 0)
    assert (await scheduler.status(uuid.uuid4()))["fast_limit"] >= 50
    # Without an explicitly configured global cap, TCP backpressure decides
    # each client's rate; the 500 kB/s client receives no reserved fifth.
    assert await scheduler.next_chunk(leases[-1], 1_048_576) == (1_048_576, 0)


@pytest.mark.asyncio
async def test_saturated_uplink_reduces_lanes_and_wakes_waiter_on_growth() -> None:
    scheduler = DownloadTrafficScheduler(uplink_bytes_per_second=100_000_000)
    leases = [uuid.uuid4() for _ in range(8)]
    for lease in leases:
        await scheduler.register(lease, uuid.uuid4(), 20_000_000_000)
    assert await scheduler.next_chunk(leases[5], 1024 * 1024) == (1024, 1024)
    pending = asyncio.create_task(scheduler.next_chunk(leases[5], 1024 * 1024))
    await asyncio.sleep(0)
    await scheduler.observe_upload(10_000_000)
    assert await scheduler.snapshot() == (7, 1)
    assert await asyncio.wait_for(pending, 0.2) == (1024 * 1024, 0)
    await scheduler.observe_upload(99_000_000)
    assert await scheduler.snapshot() == (5, 3)
    await scheduler.observe_upload(None)
    await scheduler.observe_upload(math.nan)
    assert await scheduler.snapshot() == (5, 3)


@pytest.mark.asyncio
async def test_monitor_feeds_measured_host_upload_to_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler = DownloadTrafficScheduler(uplink_bytes_per_second=100_000_000)
    for _ in range(6):
        await scheduler.register(uuid.uuid4(), uuid.uuid4(), 20_000_000_000)
    observed = asyncio.Event()

    async def snapshot(*_: object) -> NetworkThroughputSnapshot:
        observed.set()
        return NetworkThroughputSnapshot(
            status="ok",
            upload=NetworkDirection(10_000_000, (NetworkSample(datetime.now(UTC), 10_000_000),)),
        )

    monkeypatch.setattr(PrometheusNetworkClient, "snapshot", snapshot)
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        prometheus_url="http://prometheus.test",
        prometheus_connect_timeout_seconds=1.0,
        prometheus_read_timeout_seconds=3.0,
        network_interface="auto",
    )
    app.state.download_traffic_scheduler = scheduler
    monitor = asyncio.create_task(monitor_http_upload(app))
    try:
        await asyncio.wait_for(observed.wait(), 1)
        # The snapshot handler completes before the monitor's next sleep.
        await asyncio.sleep(0)
        assert await scheduler.snapshot() == (6, 0)
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor


@pytest.mark.asyncio
async def test_monitor_ignores_missing_upload_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    scheduler = DownloadTrafficScheduler(uplink_bytes_per_second=100_000_000)
    for _ in range(6):
        await scheduler.register(uuid.uuid4(), uuid.uuid4(), 20_000_000_000)
    observed = asyncio.Event()

    async def snapshot(*_: object) -> NetworkThroughputSnapshot:
        observed.set()
        return NetworkThroughputSnapshot(status="ok", upload=NetworkDirection(0, ()))

    monkeypatch.setattr(PrometheusNetworkClient, "snapshot", snapshot)
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        prometheus_url="http://prometheus.test",
        prometheus_connect_timeout_seconds=1.0,
        prometheus_read_timeout_seconds=3.0,
        network_interface="auto",
    )
    app.state.download_traffic_scheduler = scheduler
    monitor = asyncio.create_task(monitor_http_upload(app))
    try:
        await asyncio.wait_for(observed.wait(), 1)
        await asyncio.sleep(0)
        assert await scheduler.snapshot() == (5, 1)
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor


@pytest.mark.asyncio
async def test_only_two_waiting_streams_per_account_and_cleanup_frees_one() -> None:
    scheduler = DownloadTrafficScheduler()
    for _ in range(5):
        await scheduler.register(uuid.uuid4(), uuid.uuid4(), 20_000_000_000)
    owner = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    await scheduler.register(first, owner, 20_000_000_000)
    await scheduler.register(second, owner, 20_000_000_000)
    with pytest.raises(DownloadWaitingLimit):
        await scheduler.register(uuid.uuid4(), owner, 20_000_000_000)
    await scheduler.unregister(first)
    await scheduler.register(uuid.uuid4(), owner, 20_000_000_000)


@pytest.mark.asyncio
async def test_waiting_stream_wakes_on_promotion_and_small_file_gets_bounded_priority() -> None:
    current = [100.0]
    scheduler = DownloadTrafficScheduler(clock=lambda: current[0])
    fast = [uuid.uuid4() for _ in range(5)]
    for lease_id in fast:
        await scheduler.register(lease_id, uuid.uuid4(), 20_000_000_000)
    large, small = uuid.uuid4(), uuid.uuid4()
    await scheduler.register(large, uuid.uuid4(), 20_000_000_000)
    await scheduler.register(small, uuid.uuid4(), 1_000_000)
    assert (await scheduler.next_chunk(small, 1_048_576))[0] == 1024
    pending = asyncio.create_task(scheduler.next_chunk(small, 1_048_576))
    await asyncio.sleep(0)
    await scheduler.unregister(fast[0])
    assert await asyncio.wait_for(pending, timeout=0.2) == (1_048_576, 0)
    assert await scheduler.snapshot() == (5, 1)

    current[0] += 400
    await scheduler.unregister(fast[1])
    assert (await scheduler.next_chunk(large, 1_048_576))[0] == 1_048_576


@pytest.mark.asyncio
async def test_fast_stream_does_not_reserve_a_fixed_share_of_upload_budget() -> None:
    scheduler = DownloadTrafficScheduler()
    fast = [uuid.uuid4() for _ in range(5)]
    for lease_id in fast:
        await scheduler.register(lease_id, uuid.uuid4(), 20_000_000_000)
    waiting = uuid.uuid4()
    await scheduler.register(waiting, uuid.uuid4(), 20_000_000_000)
    assert await scheduler.next_chunk(fast[0], 1_048_576) == (1_048_576, 0)


@pytest.mark.asyncio
async def test_long_films_rotate_fast_lanes_without_waiting_for_completion() -> None:
    now = [0.0]
    scheduler = DownloadTrafficScheduler(clock=lambda: now[0])
    fast = [uuid.uuid4() for _ in range(5)]
    for lease_id in fast:
        await scheduler.register(lease_id, uuid.uuid4(), 20_000_000_000)
    sixth = uuid.uuid4()
    await scheduler.register(sixth, uuid.uuid4(), 20_000_000_000)

    now[0] = 121.0
    assert await scheduler.next_chunk(fast[0], 1_048_576) == (1024, 1024)
    assert await scheduler.next_chunk(sixth, 1_048_576) == (1_048_576, 0)
    assert await scheduler.snapshot() == (5, 1)


@pytest.mark.asyncio
async def test_waiting_stream_rate_is_per_file_even_on_same_account() -> None:
    limiter = DownloadRateLimiter(clock=lambda: 100.0)
    user_id = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    for stream_id in (first, second):
        assert (
            await limiter.reserve(
                user_id,
                1024,
                per_user_bytes_per_second=0,
                global_bytes_per_second=0,
                stream_id=stream_id,
                stream_bytes_per_second=1024,
            )
            == 0
        )
    assert (
        await limiter.reserve(
            user_id,
            1024,
            per_user_bytes_per_second=0,
            global_bytes_per_second=0,
            stream_id=first,
            stream_bytes_per_second=1024,
        )
        == 1
    )
