import json
import uuid
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.integrations.admin_runtime import AdminRuntimeMonitor
from app.integrations.http import IntegrationRequestError


def _registry(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "routes": [
                    {
                        "qbittorrent_account_ref": str(uuid.uuid4()),
                        "newgreedy_url": "http://newgreedy:8080",
                        "qbittorrent_url": "http://qbittorrent:8080",
                        "qbittorrent_username": "runtime-user",
                        "qbittorrent_password": "runtime-password",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_runtime_monitor_reads_private_registry_file_and_reaches_both_services(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "integration_registry"
    _registry(registry)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.")
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200, text="Ok.")
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(
                200,
                json=[
                    {
                        "hash": "a" * 40,
                        "name": "Runtime torrent",
                        "state": "downloading",
                        "progress": 0.5,
                        "total_size": 1000,
                        "downloaded": 500,
                        "uploaded": 25,
                        "dlspeed": 100,
                        "upspeed": 20,
                        "ratio": 0.05,
                        "eta": 60,
                        "category": "wos-v2",
                        "tracker": "https://tracker.example/announce",
                    }
                ],
            )
        if request.url.path == "/api/stats":
            return httpx.Response(
                200,
                json={
                    "a" * 40: {
                        "mode": "down",
                        "cumul_rep_dl": 500,
                        "cumul_rep_ul": 25,
                        "cumul_real_ul": 20,
                    }
                },
            )
        raise AssertionError(f"Unexpected runtime request: {request.method} {request.url}")

    monitor = AdminRuntimeMonitor(
        Settings(integration_accounts_file=registry),
        transport=httpx.MockTransport(handler),
    )

    _, qbittorrent, truncated = await monitor.qbittorrent_torrents()
    _, newgreedy = await monitor.newgreedy_torrents()

    assert truncated is False
    assert qbittorrent[0].name == "Runtime torrent"
    assert newgreedy[0][0].id == "a" * 40
    assert newgreedy[0][1] == "Runtime torrent"


def test_runtime_monitor_rejects_symlinked_registry(tmp_path: Path) -> None:
    registry = tmp_path / "integration_registry"
    _registry(registry)
    link = tmp_path / "registry-link"
    link.symlink_to(registry)
    monitor = AdminRuntimeMonitor(Settings(integration_accounts_file=link))

    with pytest.raises(IntegrationRequestError, match="registry is unavailable"):
        monitor._specs()


def test_runtime_monitor_bounds_registry_read_before_decoding(tmp_path: Path) -> None:
    registry = tmp_path / "integration_registry"
    registry.write_bytes(b"{" + b"x" * (64 * 1024))
    monitor = AdminRuntimeMonitor(Settings(integration_accounts_file=registry))

    with pytest.raises(IntegrationRequestError, match="registry is invalid"):
        monitor._specs()
