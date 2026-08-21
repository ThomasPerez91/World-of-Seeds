from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from app.integrations.http import IntegrationAuthenticationError
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2AddResult,
    QBittorrentV2AddState,
    QBittorrentV2Gateway,
    QBittorrentV2OwnershipError,
    QBittorrentV2RejectedError,
    QBittorrentV2TransientError,
)

INFO_HASH = "a" * 40
STORAGE_KEY = UUID("12345678-1234-5678-1234-567812345678")
SAVE_PATH = "/seedbox/content/12345678123456781234567812345678"
IDENTITY_TAG = "wos-v2-12345678123456781234567812345678"


def _gateway(client: httpx.AsyncClient) -> QBittorrentV2Gateway:
    return QBittorrentV2Gateway(
        client,
        "http://qbittorrent:8080",
        "wos",
        "secret-password",
        data_root=Path("/seedbox"),
    )


def _login_response() -> httpx.Response:
    return httpx.Response(204, headers={"Set-Cookie": "SID=test; Path=/"})


def _managed_torrent(*, category: str = "wos-v2", save_path: str = SAVE_PATH) -> dict[str, str]:
    return {
        "hash": INFO_HASH,
        "category": category,
        "save_path": save_path,
        "tags": f"wos-v2, {IDENTITY_TAG}",
    }


async def _run(handler: Callable[[httpx.Request], httpx.Response]) -> QBittorrentV2AddResult:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await _gateway(client).add_managed_torrent(
            b"normalized-torrent",
            expected_info_hash=INFO_HASH,
            storage_key=STORAGE_KEY,
        )


@pytest.mark.asyncio
async def test_gateway_adds_with_server_owned_path_category_and_identity() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            assert request.url.params["hashes"] == INFO_HASH
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/torrents/add":
            assert request.headers["cookie"] == "SID=test"
            assert b'name="savepath"' in request.content
            assert SAVE_PATH.encode() in request.content
            assert b'name="category"' in request.content
            assert b"wos-v2" in request.content
            assert b'name="tags"' in request.content
            assert IDENTITY_TAG.encode() in request.content
            return httpx.Response(
                200,
                json={
                    "success_count": 1,
                    "failure_count": 0,
                    "pending_count": 0,
                    "added_torrent_ids": [INFO_HASH],
                },
            )
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(request.url.path)

    result = await _run(handler)

    assert result.state == QBittorrentV2AddState.ADDED
    assert [request.url.path for request in requests] == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/torrents/add",
        "/api/v2/auth/logout",
    ]


@pytest.mark.asyncio
async def test_gateway_is_idempotent_when_owned_torrent_already_exists() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[_managed_torrent()])
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError("The gateway must not add the same infohash twice")

    result = await _run(handler)

    assert result.state == QBittorrentV2AddState.ALREADY_PRESENT
    assert requests == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/auth/logout",
    ]


@pytest.mark.asyncio
async def test_gateway_refuses_to_mutate_torrent_not_owned_by_wos_v2() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, json=[_managed_torrent(category="external")])
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError("The gateway must not mutate an external torrent")

    with pytest.raises(QBittorrentV2OwnershipError):
        await _run(handler)

    assert "/api/v2/torrents/add" not in requests


@pytest.mark.asyncio
async def test_gateway_reconciles_timeout_after_qbittorrent_accepted_torrent() -> None:
    lookup_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lookup_count
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            lookup_count += 1
            return httpx.Response(
                200,
                json=[] if lookup_count == 1 else [_managed_torrent()],
            )
        if request.url.path == "/api/v2/torrents/add":
            raise httpx.ReadTimeout("response lost", request=request)
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(request.url.path)

    result = await _run(handler)

    assert result.state == QBittorrentV2AddState.RECONCILED
    assert lookup_count == 2


@pytest.mark.asyncio
async def test_gateway_keeps_ambiguous_missing_add_retryable() -> None:
    lookup_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lookup_count
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            lookup_count += 1
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/torrents/add":
            raise httpx.ReadTimeout("response lost", request=request)
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(request.url.path)

    with pytest.raises(QBittorrentV2TransientError):
        await _run(handler)

    assert lookup_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, "Fails."),
        (
            200,
            '{"success_count":0,"failure_count":1,"pending_count":0,"added_torrent_ids":[]}',
        ),
        (400, "Bad request"),
    ],
)
async def test_gateway_does_not_mask_explicit_rejections(status: int, body: str) -> None:
    lookup_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal lookup_count
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            lookup_count += 1
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v2/torrents/add":
            return httpx.Response(status, text=body)
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(request.url.path)

    with pytest.raises(QBittorrentV2RejectedError):
        await _run(handler)

    assert lookup_count == 1


@pytest.mark.asyncio
async def test_gateway_preserves_authentication_error_without_sending_torrent() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(401)
        raise AssertionError(request.url.path)

    with pytest.raises(IntegrationAuthenticationError):
        await _run(handler)

    assert requests == ["/api/v2/auth/login"]


@pytest.mark.asyncio
async def test_gateway_validates_inputs_before_authentication() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"Unexpected request: {request.url.path}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = _gateway(client)
        with pytest.raises(ValueError):
            await gateway.add_managed_torrent(
                b"normalized-torrent",
                expected_info_hash="A" * 40,
                storage_key=STORAGE_KEY,
            )
        with pytest.raises(ValueError):
            await gateway.add_managed_torrent(
                b"",
                expected_info_hash=INFO_HASH,
                storage_key=STORAGE_KEY,
            )
