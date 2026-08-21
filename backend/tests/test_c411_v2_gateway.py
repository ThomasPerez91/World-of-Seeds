import hashlib
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from app.integrations.c411_v2 import (
    C411NewGreedyV2Gateway,
    C411V2ConfigurationError,
    C411V2PayloadError,
    NewGreedyV2Gateway,
    NewGreedyV2UnavailableError,
)
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2AddResult,
    QBittorrentV2AddState,
)
from app.torrents import TorrentValidationError

INFO = {
    b"length": 5,
    b"name": b"Film.mkv",
    b"piece length": 16_384,
    b"pieces": b"p" * 20,
}
INFO_HASH = hashlib.sha1(
    b"d6:lengthi5e4:name8:Film.mkv12:piece lengthi16384e6:pieces20:ppppppppppppppppppppe",
    usedforsecurity=False,
).hexdigest()
STORAGE_KEY = UUID("12345678-1234-5678-1234-567812345678")


def _bencode(value: object) -> bytes:
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(_bencode(key) + _bencode(value[key]) for key in sorted(value)) + b"e"
    raise TypeError(value)


def _torrent(*, tracker: bytes = b"https://c411.org/user/old-user-passkey") -> bytes:
    return _bencode(
        {
            b"announce": tracker,
            b"announce-list": [
                [b"https://c411.org/user/old-user-passkey"],
                [b"https://tk.c411.tw/user/old-user-passkey"],
            ],
            b"info": INFO,
        }
    )


class FakeNewGreedy:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def require_ready(self) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error


class FakeQBittorrent:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, str, UUID]] = []

    async def add_managed_torrent(
        self,
        content: bytes,
        *,
        expected_info_hash: str,
        storage_key: UUID,
    ) -> QBittorrentV2AddResult:
        self.calls.append((content, expected_info_hash, storage_key))
        return QBittorrentV2AddResult(QBittorrentV2AddState.ADDED)


def _gateway(
    qbittorrent: FakeQBittorrent,
    newgreedy: FakeNewGreedy,
) -> C411NewGreedyV2Gateway:
    return C411NewGreedyV2Gateway(
        qbittorrent,
        newgreedy,
        passkey=SecretStr("test-passkey-123"),
        allowed_tracker_hosts=["c411.org", "tk.c411.tw"],
        max_total_size=1_000,
    )


@pytest.mark.asyncio
async def test_gateway_normalizes_allowlisted_trackers_without_changing_info() -> None:
    qbittorrent = FakeQBittorrent()
    newgreedy = FakeNewGreedy()

    result = await _gateway(qbittorrent, newgreedy).add_torrent(
        _torrent(),
        expected_info_hash=INFO_HASH,
        storage_key=STORAGE_KEY,
    )

    assert result.state is QBittorrentV2AddState.ADDED
    assert newgreedy.calls == 1
    assert len(qbittorrent.calls) == 1
    normalized, passed_hash, passed_storage_key = qbittorrent.calls[0]
    assert b"old-user-passkey" not in normalized
    assert normalized.count(b"https://c411.org/announce/test-passkey-123") == 2
    assert b"https://tk.c411.tw/announce/test-passkey-123" in normalized
    assert passed_hash == INFO_HASH
    assert passed_storage_key == STORAGE_KEY
    assert _bencode(INFO) in normalized
    assert "test-passkey-123" not in repr(result)


@pytest.mark.asyncio
async def test_gateway_rejects_tracker_outside_allowlist_before_qbittorrent() -> None:
    qbittorrent = FakeQBittorrent()
    newgreedy = FakeNewGreedy()

    with pytest.raises(TorrentValidationError) as raised:
        await _gateway(qbittorrent, newgreedy).add_torrent(
            _torrent(tracker=b"https://tracker.example/user/old-user-passkey"),
            expected_info_hash=INFO_HASH,
            storage_key=STORAGE_KEY,
        )

    assert not qbittorrent.calls
    assert "old-user-passkey" not in str(raised.value)


@pytest.mark.asyncio
async def test_gateway_does_not_inject_or_submit_when_newgreedy_is_unavailable() -> None:
    qbittorrent = FakeQBittorrent()
    newgreedy = FakeNewGreedy(error=NewGreedyV2UnavailableError("NewGreedy is unavailable"))
    gateway = _gateway(qbittorrent, newgreedy)

    with pytest.raises(NewGreedyV2UnavailableError) as raised:
        await gateway.add_torrent(
            _torrent(),
            expected_info_hash=INFO_HASH,
            storage_key=STORAGE_KEY,
        )

    assert not qbittorrent.calls
    assert "test-passkey-123" not in repr(gateway)
    assert "test-passkey-123" not in str(raised.value)


@pytest.mark.asyncio
async def test_gateway_rejects_job_hash_mismatch_without_qbittorrent_mutation() -> None:
    qbittorrent = FakeQBittorrent()

    with pytest.raises(C411V2PayloadError):
        await _gateway(qbittorrent, FakeNewGreedy()).add_torrent(
            _torrent(),
            expected_info_hash="b" * 40,
            storage_key=STORAGE_KEY,
        )

    assert not qbittorrent.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(503), NewGreedyV2UnavailableError),
        (httpx.Response(200, json={"status": "ok"}), NewGreedyV2UnavailableError),
        (httpx.Response(200, content=b"{"), NewGreedyV2UnavailableError),
    ],
)
async def test_newgreedy_v2_gateway_accepts_only_bounded_valid_health(
    response: httpx.Response,
    error_type: type[Exception],
) -> None:
    transport = httpx.MockTransport(lambda _request: response)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(error_type):
            await NewGreedyV2Gateway(client, "http://newgreedy:8080").require_ready()


@pytest.mark.asyncio
async def test_newgreedy_v2_gateway_uses_only_read_only_health_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"total": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await NewGreedyV2Gateway(client, "http://newgreedy:8080").require_ready()

    assert [(request.method, request.url.path) for request in requests] == [("GET", "/api/health")]


@pytest.mark.parametrize(
    "base_url",
    [
        "https://public.example",
        "http://user:password@newgreedy:8080",
        "http://newgreedy:8080/api",
    ],
)
@pytest.mark.asyncio
async def test_newgreedy_v2_gateway_rejects_non_internal_origins(base_url: str) -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(500))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(C411V2ConfigurationError):
            NewGreedyV2Gateway(client, base_url)


@pytest.mark.parametrize(
    ("passkey", "hosts", "max_total_size"),
    [
        ("short", ["c411.org"], 1_000),
        ("test-passkey-123", [], 1_000),
        ("test-passkey-123", ["c411.org"], 0),
    ],
)
def test_gateway_rejects_invalid_infrastructure_configuration(
    passkey: str,
    hosts: list[str],
    max_total_size: int,
) -> None:
    with pytest.raises(C411V2ConfigurationError):
        C411NewGreedyV2Gateway(
            FakeQBittorrent(),
            FakeNewGreedy(),
            passkey=SecretStr(passkey),
            allowed_tracker_hosts=hosts,
            max_total_size=max_total_size,
        )
