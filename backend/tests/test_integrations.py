import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations import ExternalServicesMonitor


@pytest.mark.asyncio
async def test_monitor_probes_newgreedy_and_qbittorrent_without_leaking_credentials() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "newgreedy" and request.url.path == "/api/health":
            return httpx.Response(
                200,
                json={"total": 3, "stalled": [], "anomalies": [], "target_reached": []},
            )
        if request.url.host == "qbittorrent" and request.url.path == "/api/v2/auth/login":
            assert request.headers["referer"] == "http://qbittorrent:8080/"
            assert request.content == b"username=admin&password=secret-password"
            return httpx.Response(200, text="Ok.", headers={"Set-Cookie": "SID=test; Path=/"})
        if request.url.host == "qbittorrent" and request.url.path == "/api/v2/app/version":
            assert request.headers["cookie"] == "SID=test"
            return httpx.Response(200, text="v5.1.2")
        if request.url.host == "qbittorrent" and request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(f"Unexpected integration request: {request.method} {request.url}")

    settings = Settings.model_validate(
        {
            "newgreedy_url": "http://newgreedy:8080",
            "qbittorrent_url": "http://qbittorrent:8080",
            "qbittorrent_username": "admin",
            "qbittorrent_password": SecretStr("secret-password"),
        }
    )
    monitor = ExternalServicesMonitor(settings, transport=httpx.MockTransport(handler))

    snapshot = await monitor.snapshot()
    cached_snapshot = await monitor.snapshot()

    assert snapshot.healthy is True
    assert snapshot.newgreedy.state == "healthy"
    assert snapshot.qbittorrent.state == "healthy"
    assert snapshot.qbittorrent.version == "v5.1.2"
    assert cached_snapshot is snapshot
    assert len(requests) == 4
    assert "secret-password" not in repr(settings)


@pytest.mark.asyncio
async def test_monitor_reports_unconfigured_services_without_network_requests() -> None:
    def fail_on_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected integration request: {request.method} {request.url}")

    monitor = ExternalServicesMonitor(
        Settings(),
        transport=httpx.MockTransport(fail_on_request),
    )

    snapshot = await monitor.snapshot()

    assert snapshot.healthy is False
    assert snapshot.newgreedy.state == "unconfigured"
    assert snapshot.qbittorrent.state == "unconfigured"


@pytest.mark.asyncio
async def test_qbittorrent_authentication_failure_is_bounded_and_cached() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, text="Fails.")

    settings = Settings.model_validate(
        {
            "qbittorrent_url": "http://qbittorrent:8080",
            "qbittorrent_username": "admin",
            "qbittorrent_password": SecretStr("wrong-password"),
        }
    )
    monitor = ExternalServicesMonitor(settings, transport=httpx.MockTransport(handler))

    first = await monitor.snapshot()
    second = await monitor.snapshot()

    assert first.qbittorrent.state == "unavailable"
    assert first.qbittorrent.error_code == "authentication_failed"
    assert second is first
    assert attempts == 1
