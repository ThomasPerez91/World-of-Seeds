from collections.abc import AsyncIterator
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, Query, Response

from app.auth.dependencies import AuthContext, require_current_credentials
from app.core.config import Settings, get_settings
from app.integrations.http import integration_timeout
from app.integrations.prometheus_network import (
    NetworkDirection,
    PrometheusNetworkClient,
    PrometheusNetworkError,
)
from app.schemas.dashboard_v2 import (
    NetworkThroughputDirectionResponse,
    NetworkThroughputResponse,
    NetworkThroughputSampleResponse,
)

router = APIRouter()


def _direction(value: NetworkDirection | None) -> NetworkThroughputDirectionResponse | None:
    if value is None:
        return None
    return NetworkThroughputDirectionResponse(
        current_bytes_per_second=value.current_bytes_per_second,
        samples=[
            NetworkThroughputSampleResponse(
                timestamp=sample.timestamp,
                value_bytes_per_second=sample.value_bytes_per_second,
            )
            for sample in value.samples
        ],
    )


async def get_prometheus_network_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[PrometheusNetworkClient | None]:
    if settings.prometheus_url is None:
        yield None
        return
    timeout = integration_timeout(
        settings.prometheus_connect_timeout_seconds,
        settings.prometheus_read_timeout_seconds,
    )
    async with httpx.AsyncClient(
        base_url=str(settings.prometheus_url).rstrip("/"),
        timeout=timeout,
    ) as client:
        yield PrometheusNetworkClient(client, interface=settings.network_interface)


@router.get("/network-throughput", response_model=NetworkThroughputResponse)
async def get_network_throughput(
    response: Response,
    _context: Annotated[AuthContext, Depends(require_current_credentials)],
    prometheus: Annotated[
        PrometheusNetworkClient | None,
        Depends(get_prometheus_network_client),
    ],
    period: Annotated[Literal["realtime"], Query()] = "realtime",
) -> NetworkThroughputResponse:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    if prometheus is None:
        return NetworkThroughputResponse(status="unavailable")
    try:
        snapshot = await prometheus.snapshot(period)
    except PrometheusNetworkError:
        return NetworkThroughputResponse(status="unavailable")
    return NetworkThroughputResponse(
        status=snapshot.status,
        download=_direction(snapshot.download),
        upload=_direction(snapshot.upload),
    )
