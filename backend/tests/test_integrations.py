import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations import ExternalServicesMonitor
from app.integrations.http import IntegrationRequestError


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
async def test_qbittorrent_accepts_no_content_login_response() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(204, headers={"Set-Cookie": "SID=test; Path=/"})
        if request.url.path == "/api/v2/app/version":
            assert request.headers["cookie"] == "SID=test"
            return httpx.Response(200, text="v5.1.2")
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(f"Unexpected integration request: {request.method} {request.url}")

    monitor = ExternalServicesMonitor(
        Settings.model_validate(
            {
                "qbittorrent_url": "http://qbittorrent:8080",
                "qbittorrent_username": "admin",
                "qbittorrent_password": SecretStr("secret-password"),
            }
        ),
        transport=httpx.MockTransport(handler),
    )

    snapshot = await monitor.snapshot()

    assert snapshot.qbittorrent.state == "healthy"
    assert snapshot.qbittorrent.version == "v5.1.2"
    assert [request.url.path for request in requests] == [
        "/api/v2/auth/login",
        "/api/v2/app/version",
        "/api/v2/auth/logout",
    ]


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
    forced = await monitor.snapshot(force=True)

    assert first.qbittorrent.state == "unavailable"
    assert first.qbittorrent.error_code == "authentication_failed"
    assert second is first
    assert forced is first
    assert attempts == 1


@pytest.mark.asyncio
async def test_newgreedy_overview_and_full_stats_reset_are_validated() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/api/stats":
            return httpx.Response(
                200,
                json={
                    "deadbeef": {
                        "cumul_rep_dl": 1_000,
                        "cumul_rep_ul": 1_500,
                        "cumul_real_ul": 100,
                        "mode": "down",
                        "stalled": True,
                        "last_announce_ts": 1_700_000_000,
                    },
                    "01234567": {
                        "cumul_rep_dl": 2_000,
                        "cumul_rep_ul": 3_000,
                        "cumul_real_ul": 400,
                        "mode": "seed",
                        "target_reached": True,
                        "last_announce_ts": 1_800_000_000,
                    },
                },
            )
        if request.method == "DELETE" and request.url.path == "/api/stats/purge":
            assert dict(request.url.params) == {
                "keep_active": "false",
                "inactive_hours": "0",
            }
            return httpx.Response(200, json={"purged": 2, "remaining": 0})
        raise AssertionError(f"Unexpected integration request: {request.method} {request.url}")

    monitor = ExternalServicesMonitor(
        Settings.model_validate({"newgreedy_url": "http://newgreedy:8080"}),
        transport=httpx.MockTransport(handler),
    )

    overview = await monitor.newgreedy_overview()
    torrents = await monitor.newgreedy_torrents()
    reset = await monitor.reset_newgreedy_stats()

    assert overview.torrents == 2
    assert overview.downloading == 1
    assert overview.seeding == 1
    assert overview.stalled == 1
    assert overview.target_reached == 1
    assert overview.total_downloaded_bytes == 3_000
    assert overview.total_reported_uploaded_bytes == 4_500
    assert overview.total_fake_uploaded_bytes == 4_000
    assert [torrent.id for torrent in torrents] == ["01234567", "deadbeef"]
    assert torrents[0].mode == "seed"
    assert torrents[0].fake_uploaded_bytes == 2_600
    assert torrents[1].stalled is True
    assert reset.purged == 2
    assert reset.remaining == 0
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_qbittorrent_torrent_listing_is_normalized_and_hides_tracker_secret() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/auth/login":
            assert request.content == b"username=admin&password=secret-password"
            return httpx.Response(200, text="Ok.", headers={"Set-Cookie": "SID=test; Path=/"})
        if request.url.path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.1.2")
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        if request.url.path == "/api/v2/torrents/info":
            assert request.url.params["limit"] == "1001"
            return httpx.Response(
                200,
                json=[
                    {
                        "hash": "a" * 40,
                        "name": "Film de test.mkv",
                        "state": "downloading",
                        "progress": 0.42,
                        "total_size": 10_000,
                        "downloaded": 4_200,
                        "uploaded": 1_000,
                        "dlspeed": 2_000,
                        "upspeed": 300,
                        "ratio": 0.238,
                        "eta": 8_640_000,
                        "category": "films",
                        "tracker": "https://tracker.private.example/secret-passkey/announce",
                    }
                ],
            )
        raise AssertionError(f"Unexpected integration request: {request.method} {request.url}")

    monitor = ExternalServicesMonitor(
        Settings.model_validate(
            {
                "qbittorrent_url": "http://qbittorrent:8080",
                "qbittorrent_username": "admin",
                "qbittorrent_password": SecretStr("secret-password"),
            }
        ),
        transport=httpx.MockTransport(handler),
    )

    torrents, truncated = await monitor.qbittorrent_torrents()

    assert truncated is False
    assert len(torrents) == 1
    torrent = torrents[0]
    assert torrent.name == "Film de test.mkv"
    assert torrent.progress == 0.42
    assert torrent.eta_seconds is None
    assert torrent.tracker_host == "tracker.private.example"
    assert "secret-passkey" not in repr(torrent)
    assert len(requests) == 6


@pytest.mark.asyncio
async def test_qbittorrent_torrent_listing_respects_cached_authentication_failure() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, text="Fails.")

    monitor = ExternalServicesMonitor(
        Settings.model_validate(
            {
                "qbittorrent_url": "http://qbittorrent:8080",
                "qbittorrent_username": "admin",
                "qbittorrent_password": SecretStr("wrong-password"),
            }
        ),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(IntegrationRequestError, match="unavailable"):
        await monitor.qbittorrent_torrents()
    with pytest.raises(IntegrationRequestError, match="unavailable"):
        await monitor.qbittorrent_torrents()

    assert attempts == 1
