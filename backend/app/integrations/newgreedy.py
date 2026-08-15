from time import perf_counter

import httpx

from app.integrations.http import IntegrationRequestError, read_limited_json
from app.integrations.types import ServiceProbe


class NewGreedyClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def probe(self) -> ServiceProbe:
        started_at = perf_counter()
        try:
            async with self._client.stream("GET", f"{self._base_url}/api/health") as response:
                response.raise_for_status()
                payload = await read_limited_json(response, max_bytes=256 * 1024)
            if not isinstance(payload, dict) or not isinstance(payload.get("total"), int):
                raise IntegrationRequestError("NewGreedy health payload is invalid")
        except (httpx.HTTPError, IntegrationRequestError):
            return ServiceProbe(
                service="newgreedy",
                state="unavailable",
                latency_ms=_latency_ms(started_at),
                error_code="request_failed",
            )
        return ServiceProbe(
            service="newgreedy",
            state="healthy",
            latency_ms=_latency_ms(started_at),
        )


def _latency_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))
