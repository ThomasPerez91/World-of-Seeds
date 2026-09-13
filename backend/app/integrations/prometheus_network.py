from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx

from app.integrations.http import IntegrationRequestError, read_limited_bytes

NetworkPeriod = Literal["realtime"]

MAX_PROMETHEUS_RESPONSE_BYTES = 1024 * 1024
MAX_PROMETHEUS_SERIES = 64
MAX_PROMETHEUS_POINTS_PER_SERIES = 64
NETWORK_HISTORY = timedelta(minutes=5)
NETWORK_RATE_WINDOW = "1m"
NETWORK_STEP_SECONDS = 15
NETWORK_FRESHNESS = timedelta(seconds=NETWORK_STEP_SECONDS * 3)
_DEVICE_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,32}$")
_VIRTUAL_DEVICE = re.compile(
    r"^(?:lo|docker.*|br-[0-9a-f]+|veth.*|virbr.*|tun\d*|tap\d*|wg\d*|"
    r"tailscale\d*|cni.*|flannel.*|kube.*)$",
    re.IGNORECASE,
)


class PrometheusNetworkError(RuntimeError):
    """Raised when the bounded Prometheus response cannot be trusted."""


@dataclass(frozen=True, slots=True)
class NetworkSample:
    timestamp: datetime
    value_bytes_per_second: float


@dataclass(frozen=True, slots=True)
class NetworkDirection:
    current_bytes_per_second: float
    samples: tuple[NetworkSample, ...]


@dataclass(frozen=True, slots=True)
class NetworkThroughputSnapshot:
    status: Literal["ok", "no_data"]
    download: NetworkDirection | None = None
    upload: NetworkDirection | None = None


class PrometheusNetworkClient:
    def __init__(self, client: httpx.AsyncClient, *, interface: str = "auto") -> None:
        self._client = client
        self._interface = interface

    async def snapshot(
        self,
        period: NetworkPeriod,
        *,
        now: datetime | None = None,
    ) -> NetworkThroughputSnapshot:
        if period != "realtime":
            raise ValueError("unsupported network period")
        end = (now or datetime.now(UTC)).astimezone(UTC)
        start = end - NETWORK_HISTORY
        receive_query = _network_query("receive")
        transmit_query = _network_query("transmit")
        try:
            receive, transmit = await asyncio.gather(
                self._query_range(receive_query, start=start, end=end),
                self._query_range(transmit_query, start=start, end=end),
            )
        except (httpx.HTTPError, IntegrationRequestError, ValueError) as exc:
            raise PrometheusNetworkError("prometheus network query failed") from exc

        receive = _fresh_series(receive, end=end)
        transmit = _fresh_series(transmit, end=end)
        device = _select_device(receive, transmit, configured=self._interface)
        if device is None:
            return NetworkThroughputSnapshot(status="no_data")
        download = _direction(receive.get(device, ()))
        upload = _direction(transmit.get(device, ()))
        if download is None and upload is None:
            return NetworkThroughputSnapshot(status="no_data")
        return NetworkThroughputSnapshot(
            status="ok",
            download=download or NetworkDirection(0, ()),
            upload=upload or NetworkDirection(0, ()),
        )

    async def _query_range(
        self,
        query: str,
        *,
        start: datetime,
        end: datetime,
    ) -> dict[str, tuple[NetworkSample, ...]]:
        async with self._client.stream(
            "GET",
            "/api/v1/query_range",
            params={
                "query": query,
                "start": f"{start.timestamp():.3f}",
                "end": f"{end.timestamp():.3f}",
                "step": str(NETWORK_STEP_SECONDS),
            },
        ) as response:
            response.raise_for_status()
            payload = json.loads(
                (
                    await read_limited_bytes(
                        response,
                        max_bytes=MAX_PROMETHEUS_RESPONSE_BYTES,
                    )
                ).decode("utf-8")
            )
        return _parse_matrix(payload)


def _network_query(direction: Literal["receive", "transmit"]) -> str:
    metric = f"node_network_{direction}_bytes_total"
    excluded = (
        r"^(lo|docker.*|br-[0-9a-f]+|veth.*|virbr.*|tun[0-9]*|tap[0-9]*|"
        r"wg[0-9]*|tailscale[0-9]*|cni.*|flannel.*|kube.*)$"
    )
    return f'rate({metric}{{job="node-exporter",device!~"{excluded}"}}[{NETWORK_RATE_WINDOW}])'


def _parse_matrix(payload: object) -> dict[str, tuple[NetworkSample, ...]]:
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("prometheus response is unsuccessful")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "matrix":
        raise ValueError("prometheus response is not a matrix")
    results = data.get("result")
    if not isinstance(results, list) or len(results) > MAX_PROMETHEUS_SERIES:
        raise ValueError("prometheus series are invalid")
    parsed: dict[str, tuple[NetworkSample, ...]] = {}
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("prometheus series is invalid")
        metric = result.get("metric")
        values = result.get("values")
        device = metric.get("device") if isinstance(metric, dict) else None
        if (
            not isinstance(device, str)
            or _DEVICE_NAME.fullmatch(device) is None
            or _VIRTUAL_DEVICE.fullmatch(device) is not None
            or not isinstance(values, list)
            or len(values) > MAX_PROMETHEUS_POINTS_PER_SERIES
        ):
            continue
        samples: list[NetworkSample] = []
        for raw in values:
            if not isinstance(raw, list) or len(raw) != 2:
                raise ValueError("prometheus sample is invalid")
            try:
                timestamp = float(raw[0])
                value = float(raw[1])
            except (TypeError, ValueError) as exc:
                raise ValueError("prometheus sample is invalid") from exc
            if not math.isfinite(timestamp) or not math.isfinite(value):
                continue
            samples.append(
                NetworkSample(
                    timestamp=datetime.fromtimestamp(timestamp, tz=UTC),
                    value_bytes_per_second=max(0.0, value),
                )
            )
        if samples:
            parsed[device] = tuple(samples)
    return parsed


def _select_device(
    receive: dict[str, tuple[NetworkSample, ...]],
    transmit: dict[str, tuple[NetworkSample, ...]],
    *,
    configured: str,
) -> str | None:
    candidates = set(receive) | set(transmit)
    if configured != "auto":
        return configured if configured in candidates else None
    if not candidates:
        return None

    def score(device: str) -> tuple[float, int, str]:
        rx = receive.get(device, ())
        tx = transmit.get(device, ())
        current = (rx[-1].value_bytes_per_second if rx else 0) + (
            tx[-1].value_bytes_per_second if tx else 0
        )
        aggregate_rank = int(device.lower().startswith(("bond", "br")))
        return current, aggregate_rank, device

    return max(candidates, key=score)


def _fresh_series(
    series: dict[str, tuple[NetworkSample, ...]],
    *,
    end: datetime,
) -> dict[str, tuple[NetworkSample, ...]]:
    earliest = end - NETWORK_FRESHNESS
    latest = end + timedelta(seconds=NETWORK_STEP_SECONDS)
    return {
        device: samples
        for device, samples in series.items()
        if samples and earliest <= samples[-1].timestamp <= latest
    }


def _direction(samples: tuple[NetworkSample, ...]) -> NetworkDirection | None:
    if not samples:
        return None
    return NetworkDirection(
        current_bytes_per_second=samples[-1].value_bytes_per_second,
        samples=samples,
    )
