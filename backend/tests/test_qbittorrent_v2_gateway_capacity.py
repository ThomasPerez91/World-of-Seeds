import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from app.integrations.qbittorrent_v2 import (
    QBittorrentV2DesiredControl,
    QBittorrentV2Gateway,
    QBittorrentV2RunState,
)


@pytest.mark.asyncio
async def test_gateway_accepts_full_200_torrent_control_snapshot_above_256_kib() -> None:
    controls: list[QBittorrentV2DesiredControl] = []
    records: list[dict[str, object]] = []

    for index in range(1, 201):
        storage_key = UUID(int=index)
        info_hash = f"{index:040x}"
        controls.append(
            QBittorrentV2DesiredControl(
                info_hash=info_hash,
                storage_key=storage_key,
                run_state=QBittorrentV2RunState.STOPPED,
                download_limit_bytes_per_second=0,
            )
        )
        records.append(
            {
                "hash": info_hash,
                "category": "wos-v2",
                "save_path": f"/seedbox/content/{storage_key.hex}",
                "tags": f"wos-v2, wos-v2-{storage_key.hex}",
                "state": "pausedDL",
                "dl_limit": -1,
                # qBittorrent returns many fields that WOS does not consume. Keep
                # realistic ignored payload here so the 200-item response exceeds
                # the former 256 KiB bound that failed during the Rise2 pilot.
                "name": "x" * 2048,
            }
        )

    payload_size = len(json.dumps(records).encode())
    assert payload_size > 256 * 1024
    assert payload_size < 2 * 1024 * 1024

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(204, headers={"Set-Cookie": "SID=test; Path=/"})
        if request.url.path == "/api/v2/torrents/info":
            assert len(request.url.params["hashes"].split("|")) == 200
            return httpx.Response(200, json=records)
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(f"unexpected qBittorrent mutation: {request.url.path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = QBittorrentV2Gateway(
            client,
            "http://qbittorrent:8080",
            "wos",
            "secret-password",
            data_root=Path("/seedbox"),
        )
        result = await gateway.apply_managed_controls(tuple(controls))

    assert result.started == ()
    assert result.stopped == ()
    assert result.limits_updated == ()
    assert result.priorities_applied == ()
    assert requests == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/auth/logout",
    ]
