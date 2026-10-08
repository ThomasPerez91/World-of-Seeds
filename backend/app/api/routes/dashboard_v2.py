from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, Response

from app.auth.dependencies import AuthContext, require_current_credentials
from app.core.config import Settings, get_settings
from app.integrations.network_collection import NetworkThroughputCollector
from app.integrations.prometheus_network import (
    NetworkDirection,
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
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> NetworkThroughputCollector | None:
    if settings.prometheus_url is None:
        return None
    return cast(NetworkThroughputCollector, request.app.state.network_throughput_collector)


@router.get("/network-throughput", response_model=NetworkThroughputResponse)
async def get_network_throughput(
    response: Response,
    _context: Annotated[AuthContext, Depends(require_current_credentials)],
    prometheus: Annotated[
        NetworkThroughputCollector | None,
        Depends(get_prometheus_network_client),
    ],
    period: Annotated[Literal["realtime"], Query()] = "realtime",
) -> NetworkThroughputResponse:
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
