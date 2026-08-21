from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from app.integrations.http import IntegrationAuthenticationError
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2AddResult,
    QBittorrentV2AddState,
    QBittorrentV2DesiredControl,
    QBittorrentV2Gateway,
    QBittorrentV2ManagedIdentity,
    QBittorrentV2OwnershipError,
    QBittorrentV2RejectedError,
    QBittorrentV2RunState,
    QBittorrentV2TransientError,
)

INFO_HASH = "a" * 40
STORAGE_KEY = UUID("12345678-1234-5678-1234-567812345678")
SAVE_PATH = "/seedbox/content/12345678123456781234567812345678"
IDENTITY_TAG = "wos-v2-12345678123456781234567812345678"
INFO_HASH_B = "b" * 40
STORAGE_KEY_B = UUID("87654321-4321-8765-4321-876543218765")
SAVE_PATH_B = "/seedbox/content/87654321432187654321876543218765"
IDENTITY_TAG_B = "wos-v2-87654321432187654321876543218765"


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


def _control_torrent(
    info_hash: str,
    *,
    save_path: str,
    identity_tag: str,
    state: str,
    limit: int,
    category: str = "wos-v2",
) -> dict[str, str | int]:
    return {
        "hash": info_hash,
        "category": category,
        "save_path": save_path,
        "tags": f"wos-v2, {identity_tag}",
        "state": state,
        "dl_limit": limit,
    }


def _controls() -> tuple[QBittorrentV2DesiredControl, ...]:
    return (
        QBittorrentV2DesiredControl(
            info_hash=INFO_HASH,
            storage_key=STORAGE_KEY,
            run_state=QBittorrentV2RunState.RUNNING,
            download_limit_bytes_per_second=51,
        ),
        QBittorrentV2DesiredControl(
            info_hash=INFO_HASH_B,
            storage_key=STORAGE_KEY_B,
            run_state=QBittorrentV2RunState.STOPPED,
            download_limit_bytes_per_second=0,
        ),
    )


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
async def test_gateway_reads_bounded_owned_torrent_state_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(
                200,
                json=[
                    {
                        **_managed_torrent(),
                        "state": "stalledDL",
                        "progress": 0.25,
                    }
                ],
            )
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(request.url.path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        snapshots = await _gateway(client).inspect_managed_torrents(
            (QBittorrentV2ManagedIdentity(INFO_HASH, STORAGE_KEY),)
        )

    assert [(item.info_hash, item.state, item.progress) for item in snapshots] == [
        (INFO_HASH, "stalledDL", 0.25)
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


@pytest.mark.asyncio
async def test_gateway_reconciles_qb5_state_limit_and_priority_in_safe_order() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        requests.append((request.url.path, body))
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            assert request.url.params["hashes"] == f"{INFO_HASH}|{INFO_HASH_B}"
            return httpx.Response(
                200,
                json=[
                    _control_torrent(
                        INFO_HASH,
                        save_path=SAVE_PATH,
                        identity_tag=IDENTITY_TAG,
                        state="stoppedDL",
                        limit=-1,
                    ),
                    _control_torrent(
                        INFO_HASH_B,
                        save_path=SAVE_PATH_B,
                        identity_tag=IDENTITY_TAG_B,
                        state="downloading",
                        limit=99,
                    ),
                ],
            )
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        if request.url.path.startswith("/api/v2/torrents/"):
            return httpx.Response(200)
        raise AssertionError(request.url.path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _gateway(client).apply_managed_controls(_controls())

    assert result.started == (INFO_HASH,)
    assert result.stopped == (INFO_HASH_B,)
    assert result.limits_updated == (INFO_HASH, INFO_HASH_B)
    assert result.priorities_applied == (INFO_HASH,)
    assert requests == [
        ("/api/v2/auth/login", "username=wos&password=secret-password"),
        ("/api/v2/torrents/info", ""),
        ("/api/v2/torrents/stop", f"hashes={INFO_HASH_B}"),
        ("/api/v2/torrents/setDownloadLimit", f"hashes={INFO_HASH_B}&limit=0"),
        ("/api/v2/torrents/setDownloadLimit", f"hashes={INFO_HASH}&limit=51"),
        ("/api/v2/torrents/topPrio", f"hashes={INFO_HASH}"),
        ("/api/v2/torrents/start", f"hashes={INFO_HASH}"),
        ("/api/v2/auth/logout", ""),
    ]


@pytest.mark.asyncio
async def test_gateway_replay_skips_state_and_limit_writes_but_reasserts_priority() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(
                200,
                json=[
                    _control_torrent(
                        INFO_HASH,
                        save_path=SAVE_PATH,
                        identity_tag=IDENTITY_TAG,
                        state="downloading",
                        limit=51,
                    ),
                    _control_torrent(
                        INFO_HASH_B,
                        save_path=SAVE_PATH_B,
                        identity_tag=IDENTITY_TAG_B,
                        state="pausedDL",
                        limit=-1,
                    ),
                ],
            )
        if request.url.path in {"/api/v2/torrents/topPrio", "/api/v2/auth/logout"}:
            return httpx.Response(200)
        raise AssertionError(request.url.path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _gateway(client).apply_managed_controls(_controls())

    assert result.started == result.stopped == result.limits_updated == ()
    assert requests == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/torrents/topPrio",
        "/api/v2/auth/logout",
    ]


@pytest.mark.asyncio
async def test_gateway_validates_entire_batch_ownership_before_mutation() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(
                200,
                json=[
                    _control_torrent(
                        INFO_HASH,
                        save_path=SAVE_PATH,
                        identity_tag=IDENTITY_TAG,
                        state="stoppedDL",
                        limit=-1,
                    ),
                    _control_torrent(
                        INFO_HASH_B,
                        save_path=SAVE_PATH_B,
                        identity_tag=IDENTITY_TAG_B,
                        state="downloading",
                        limit=99,
                        category="external",
                    ),
                ],
            )
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError("No torrent mutation is allowed before full ownership validation")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(QBittorrentV2OwnershipError):
            await _gateway(client).apply_managed_controls(_controls())

    assert requests == [
        "/api/v2/auth/login",
        "/api/v2/torrents/info",
        "/api/v2/auth/logout",
    ]


@pytest.mark.asyncio
async def test_gateway_preserves_explicit_qb_control_rejection() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/auth/login":
            return _login_response()
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(
                200,
                json=[
                    _control_torrent(
                        INFO_HASH,
                        save_path=SAVE_PATH,
                        identity_tag=IDENTITY_TAG,
                        state="downloading",
                        limit=51,
                    ),
                    _control_torrent(
                        INFO_HASH_B,
                        save_path=SAVE_PATH_B,
                        identity_tag=IDENTITY_TAG_B,
                        state="pausedDL",
                        limit=-1,
                    ),
                ],
            )
        if request.url.path == "/api/v2/torrents/topPrio":
            return httpx.Response(409, text="Torrent queueing is disabled")
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(request.url.path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(QBittorrentV2RejectedError):
            await _gateway(client).apply_managed_controls(_controls())
