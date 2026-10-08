import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import Request
from pydantic import AnyHttpUrl

from app.api.routes.dashboard_v2 import get_prometheus_network_client
from app.core.config import Settings
from app.integrations import network_collection
from app.integrations.network_collection import NetworkThroughputCollector
from app.integrations.prometheus_network import (
    NetworkDirection,
    NetworkSample,
    NetworkThroughputSnapshot,
    PrometheusNetworkClient,
    PrometheusNetworkError,
)
from app.main import create_app


@pytest.mark.asyncio
async def test_fifty_readers_share_two_bounded_prometheus_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    now = [100.0]
    monkeypatch.setattr(network_collection, "monotonic", lambda: now[0])

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await asyncio.sleep(0)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"device": "eno1"},
                            "values": [[datetime.now(UTC).timestamp(), "1024"]],
                        }
                    ],
                },
            },
        )

    async with httpx.AsyncClient(
        base_url="http://prometheus.test", transport=httpx.MockTransport(handler)
    ) as client:
        collector = NetworkThroughputCollector(
            Settings(prometheus_url=AnyHttpUrl("http://prometheus.test"))
        )
        collector._client = client
        snapshots = await asyncio.gather(*(collector.snapshot("realtime") for _ in range(50)))
        assert len(requests) == 2
        assert all(value is snapshots[0] for value in snapshots)
        assert snapshots[0].upload is not None
        assert snapshots[0].upload.current_bytes_per_second == 1024
        now[0] = 114.9
        assert await collector.snapshot("realtime") is snapshots[0]
        now[0] = 115.0
        assert await collector.snapshot("realtime") is not snapshots[0]
        assert len(requests) == 4
        assert all(request.url.path == "/api/v1/query_range" for request in requests)
        await collector.aclose()
        assert client.is_closed
        assert collector._snapshot is None


@pytest.mark.asyncio
async def test_failure_is_shared_and_recovery_never_serves_expired_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    calls = 0
    fail = False
    monkeypatch.setattr(network_collection, "monotonic", lambda: clock[0])

    async def load(*args: object) -> NetworkThroughputSnapshot:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        if fail:
            raise PrometheusNetworkError("test failure")
        return NetworkThroughputSnapshot(status="no_data")

    monkeypatch.setattr(PrometheusNetworkClient, "snapshot", load)
    collector = NetworkThroughputCollector(
        Settings(prometheus_url=AnyHttpUrl("http://prometheus.test"))
    )
    try:
        assert (await collector.snapshot("realtime")).status == "no_data"
        fail = True
        clock[0] = 15.0
        results = await asyncio.gather(
            *(collector.snapshot("realtime") for _ in range(50)), return_exceptions=True
        )
        assert all(isinstance(value, PrometheusNetworkError) for value in results)
        assert calls == 2
        assert collector._snapshot is None
        fail = False
        clock[0] = 19.9
        with pytest.raises(PrometheusNetworkError):
            await collector.snapshot("realtime")
        clock[0] = 20.0
        assert (await collector.snapshot("realtime")).status == "no_data"
        assert calls == 3
    finally:
        await collector.aclose()


@pytest.mark.asyncio
async def test_cache_cannot_extend_sample_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    calls = 0
    monkeypatch.setattr(network_collection, "monotonic", lambda: clock[0])
    wall_now = datetime.now(UTC)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> "FixedDatetime":
            return cls.fromtimestamp(wall_now.timestamp(), UTC)

    monkeypatch.setattr(network_collection, "datetime", FixedDatetime)

    async def load(*args: object) -> NetworkThroughputSnapshot:
        nonlocal calls
        calls += 1
        sample = NetworkSample(wall_now - timedelta(seconds=44), 100.0)
        return NetworkThroughputSnapshot(status="ok", upload=NetworkDirection(100.0, (sample,)))

    monkeypatch.setattr(PrometheusNetworkClient, "snapshot", load)
    collector = NetworkThroughputCollector(
        Settings(prometheus_url=AnyHttpUrl("http://prometheus.test"))
    )
    try:
        first = await collector.snapshot("realtime")
        clock[0] = 0.9
        assert await collector.snapshot("realtime") is first
        clock[0] = 1.0
        assert await collector.snapshot("realtime") is not first
        assert calls == 2
    finally:
        await collector.aclose()


@pytest.mark.asyncio
async def test_cancelled_reader_releases_collection_without_poisoning_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    calls = 0

    async def load(*args: object) -> NetworkThroughputSnapshot:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return NetworkThroughputSnapshot(status="no_data")

    monkeypatch.setattr(PrometheusNetworkClient, "snapshot", load)
    collector = NetworkThroughputCollector(
        Settings(prometheus_url=AnyHttpUrl("http://prometheus.test"))
    )
    try:
        reader = asyncio.create_task(collector.snapshot("realtime"))
        await asyncio.wait_for(entered.wait(), 1)
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader
        result = await asyncio.wait_for(collector.snapshot("realtime"), 1)
        assert result.status == "no_data"
        assert calls == 2
    finally:
        await collector.aclose()


@pytest.mark.asyncio
async def test_dashboard_dependency_reuses_application_collector_and_isolates_apps() -> None:
    settings = Settings(prometheus_url=AnyHttpUrl("http://prometheus.test"))
    first = create_app(settings)
    second = create_app(settings)
    request = Request({"type": "http", "app": first})
    shared = await get_prometheus_network_client(request, settings)
    assert shared is not None
    assert shared is first.state.network_throughput_collector
    assert shared is not second.state.network_throughput_collector
    assert await get_prometheus_network_client(request, Settings()) is None
    await first.state.network_throughput_collector.aclose()
    await second.state.network_throughput_collector.aclose()


@pytest.mark.asyncio
async def test_http_monitor_reuses_dashboard_sample_with_its_real_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import suppress
    from unittest.mock import AsyncMock

    from app.main import monitor_http_upload

    calls = 0
    sampled_at = datetime.now(UTC) - timedelta(seconds=30)

    async def load(*args: object) -> NetworkThroughputSnapshot:
        nonlocal calls
        calls += 1
        return NetworkThroughputSnapshot(
            status="ok", upload=NetworkDirection(1024, (NetworkSample(sampled_at, 1024),))
        )

    monkeypatch.setattr(PrometheusNetworkClient, "snapshot", load)
    settings = Settings(prometheus_url=AnyHttpUrl("http://prometheus.test"))
    application = create_app(settings)
    collector = application.state.network_throughput_collector
    await collector.snapshot("realtime")
    observed = asyncio.Event()

    async def observe(*args: object, **kwargs: object) -> None:
        observed.set()

    application.state.download_traffic_scheduler.observe_upload = AsyncMock(side_effect=observe)
    monitor = asyncio.create_task(monitor_http_upload(application))
    try:
        await asyncio.wait_for(observed.wait(), 1)
        assert calls == 1
        args, kwargs = application.state.download_traffic_scheduler.observe_upload.call_args
        assert args == (1024,)
        assert 30 <= kwargs["sample_age_seconds"] < 31
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor
        await collector.aclose()
