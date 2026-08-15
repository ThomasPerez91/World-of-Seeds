from contextlib import suppress
from time import perf_counter

import httpx

from app.integrations.http import IntegrationRequestError, read_limited_text
from app.integrations.types import ServiceProbe

MAX_VERSION_BYTES = 1024


class QBittorrentClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        username: str,
        password: str,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password

    async def probe(self) -> ServiceProbe:
        started_at = perf_counter()
        logged_in = False
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
                headers=self._browser_headers(),
            ) as login_response:
                login_response.raise_for_status()
                login_result = await read_limited_text(login_response, max_bytes=MAX_VERSION_BYTES)
            if login_result.strip() != "Ok.":
                return ServiceProbe(
                    service="qbittorrent",
                    state="unavailable",
                    latency_ms=_latency_ms(started_at),
                    error_code="authentication_failed",
                )
            logged_in = True
            async with self._client.stream(
                "GET",
                f"{self._base_url}/api/v2/app/version",
                headers=self._browser_headers(),
            ) as version_response:
                version_response.raise_for_status()
                version = (
                    await read_limited_text(version_response, max_bytes=MAX_VERSION_BYTES)
                ).strip()
            if version == "":
                raise ValueError("qBittorrent version response is empty")
        except (httpx.HTTPError, IntegrationRequestError, ValueError):
            return ServiceProbe(
                service="qbittorrent",
                state="unavailable",
                latency_ms=_latency_ms(started_at),
                error_code="request_failed",
            )
        finally:
            if logged_in:
                with suppress(httpx.HTTPError):
                    await self._client.post(
                        f"{self._base_url}/api/v2/auth/logout",
                        headers=self._browser_headers(),
                    )

        return ServiceProbe(
            service="qbittorrent",
            state="healthy",
            latency_ms=_latency_ms(started_at),
            version=version,
        )

    def _browser_headers(self) -> dict[str, str]:
        return {
            "Origin": self._base_url,
            "Referer": f"{self._base_url}/",
        }


def _latency_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))
