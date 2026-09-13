from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.dashboard_v2 import get_prometheus_network_client
from app.auth.security import hash_password
from app.integrations.prometheus_network import (
    NetworkDirection,
    NetworkSample,
    NetworkThroughputSnapshot,
    PrometheusNetworkClient,
    PrometheusNetworkError,
)
from app.main import app
from app.models import User

NOW = datetime(2026, 9, 13, 10, 30, tzinfo=UTC)


def _matrix(series: list[tuple[str, list[tuple[float, str]]]]) -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"device": device},
                    "values": [[timestamp, value] for timestamp, value in values],
                }
                for device, values in series
            ],
        },
    }


@pytest.mark.asyncio
async def test_prometheus_parses_rx_tx_series_and_uses_only_bounded_queries() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        query = request.url.params["query"]
        values = [(NOW.timestamp() - 15, "1024"), (NOW.timestamp(), "2048")]
        if "receive" in query:
            return httpx.Response(200, json=_matrix([("eno1", values)]))
        return httpx.Response(200, json=_matrix([("eno1", [(NOW.timestamp(), "512")])]))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://prometheus:9090",
    ) as client:
        snapshot = await PrometheusNetworkClient(client).snapshot("realtime", now=NOW)

    assert snapshot.status == "ok"
    assert snapshot.download is not None
    assert snapshot.download.current_bytes_per_second == 2048
    assert [sample.value_bytes_per_second for sample in snapshot.download.samples] == [1024, 2048]
    assert snapshot.upload is not None and snapshot.upload.current_bytes_per_second == 512
    assert len(requests) == 2
    assert all(request.url.path == "/api/v1/query_range" for request in requests)
    assert all(request.url.params["step"] == "15" for request in requests)
    assert all("[1m]" in request.url.params["query"] for request in requests)
    assert all("device!~" in request.url.params["query"] for request in requests)


@pytest.mark.asyncio
async def test_prometheus_excludes_virtual_devices_and_normalizes_negative_rates() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=_matrix(
                [
                    ("docker0", [(NOW.timestamp(), "9000")]),
                    ("veth123", [(NOW.timestamp(), "8000")]),
                    ("eno1", [(NOW.timestamp(), "-12")]),
                ]
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://prometheus:9090",
    ) as client:
        snapshot = await PrometheusNetworkClient(client).snapshot("realtime", now=NOW)

    assert calls == 2
    assert snapshot.status == "ok"
    assert snapshot.download is not None and snapshot.download.current_bytes_per_second == 0
    assert snapshot.upload is not None and snapshot.upload.current_bytes_per_second == 0


@pytest.mark.asyncio
async def test_prometheus_prefers_one_aggregate_device_on_equal_traffic() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        receive = "receive" in request.url.params["query"]
        return httpx.Response(
            200,
            json=_matrix(
                [
                    ("enp1s0", [(NOW.timestamp(), "9" if receive else "1")]),
                    ("bond0", [(NOW.timestamp(), "5")]),
                ]
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://prometheus:9090",
    ) as client:
        snapshot = await PrometheusNetworkClient(client).snapshot("realtime", now=NOW)

    assert snapshot.download is not None and snapshot.download.current_bytes_per_second == 5
    assert snapshot.upload is not None and snapshot.upload.current_bytes_per_second == 5


@pytest.mark.asyncio
async def test_prometheus_honors_configured_interface_without_summing_devices() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_matrix(
                [
                    ("eno1", [(NOW.timestamp(), "100")]),
                    ("eno2", [(NOW.timestamp(), "200")]),
                ]
            ),
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://prometheus:9090",
    ) as client:
        snapshot = await PrometheusNetworkClient(client, interface="eno1").snapshot(
            "realtime", now=NOW
        )

    assert snapshot.download is not None and snapshot.download.current_bytes_per_second == 100
    assert snapshot.upload is not None and snapshot.upload.current_bytes_per_second == 100


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        lambda _request: httpx.Response(503),
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request)),
    ],
)
async def test_prometheus_failure_and_timeout_are_controlled(handler: object) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        base_url="http://prometheus:9090",
    ) as client:
        with pytest.raises(PrometheusNetworkError):
            await PrometheusNetworkClient(client).snapshot("realtime", now=NOW)


@pytest.mark.asyncio
async def test_prometheus_empty_response_is_no_data() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=_matrix([])))
    async with httpx.AsyncClient(transport=transport, base_url="http://prometheus:9090") as client:
        snapshot = await PrometheusNetworkClient(client).snapshot("realtime", now=NOW)
    assert snapshot == NetworkThroughputSnapshot(status="no_data")


@pytest.mark.asyncio
async def test_prometheus_rejects_stale_series_as_no_data() -> None:
    stale = NOW.timestamp() - 60
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=_matrix([("eno1", [(stale, "4096")])]))
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://prometheus:9090") as client:
        snapshot = await PrometheusNetworkClient(client).snapshot("realtime", now=NOW)

    assert snapshot == NetworkThroughputSnapshot(status="no_data")


class _OversizedStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.chunks_read = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for _ in range(10):
            self.chunks_read += 1
            yield b"x" * (512 * 1024)


@pytest.mark.asyncio
async def test_prometheus_stops_streaming_an_oversized_response() -> None:
    streams: list[_OversizedStream] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        stream = _OversizedStream()
        streams.append(stream)
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://prometheus:9090",
    ) as client:
        with pytest.raises(PrometheusNetworkError):
            await PrometheusNetworkClient(client).snapshot("realtime", now=NOW)

    assert streams
    assert all(stream.chunks_read <= 3 for stream in streams)


class _FakePrometheus:
    def __init__(self, snapshot: NetworkThroughputSnapshot) -> None:
        self.snapshot_value = snapshot
        self.periods: list[str] = []

    async def snapshot(self, period: str) -> NetworkThroughputSnapshot:
        self.periods.append(period)
        return self.snapshot_value


async def _login(client: AsyncClient, db_session: AsyncSession) -> None:
    db_session.add(User(username="thomas", password_hash=hash_password("correct-horse-battery")))
    await db_session.commit()
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "thomas", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_network_endpoint_requires_authentication_and_rejects_invalid_period(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    assert (await client.get("/api/v2/dashboard/network-throughput")).status_code == 401
    await _login(client, db_session)
    response = await client.get("/api/v2/dashboard/network-throughput?period=24h")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_network_endpoint_exposes_only_bounded_throughput_data(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    sample = NetworkSample(timestamp=NOW, value_bytes_per_second=4096)
    fake = _FakePrometheus(
        NetworkThroughputSnapshot(
            status="ok",
            download=NetworkDirection(4096, (sample,)),
            upload=NetworkDirection(1024, (sample,)),
        )
    )

    async def override() -> _FakePrometheus:
        return fake

    app.dependency_overrides[get_prometheus_network_client] = override
    try:
        await _login(client, db_session)
        response = await client.get("/api/v2/dashboard/network-throughput?period=realtime&query=up")
    finally:
        app.dependency_overrides.pop(get_prometheus_network_client, None)

    assert response.status_code == 200
    assert fake.periods == ["realtime"]
    payload = response.json()
    assert set(payload) == {
        "status",
        "period",
        "sample_interval_seconds",
        "download",
        "upload",
    }
    assert payload["download"]["current_bytes_per_second"] == 4096
    assert "query" not in payload


@pytest.mark.asyncio
async def test_network_endpoint_returns_controlled_unavailable_state(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _login(client, db_session)
    response = await client.get("/api/v2/dashboard/network-throughput")
    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
