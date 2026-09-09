import hashlib
from pathlib import Path

import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.core.config import Settings, get_settings
from app.files import WorkspaceManager
from app.integrations.http import IntegrationRequestError
from app.integrations.types import QBittorrentTorrent
from app.main import app
from app.models import User, UserTorrent
from app.torrents import TorrentValidationError, normalize_torrent


def bencode(value: object) -> bytes:
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(key) + bencode(value[key]) for key in sorted(value)) + b"e"
    raise TypeError(value)


def torrent_content(
    *,
    name: bytes = b"Film.mkv",
    tracker: bytes = b"https://c411.org/anything/old-user-passkey",
    announce_list: list[list[bytes]] | None = None,
) -> bytes:
    info = {
        b"length": 5,
        b"name": name,
        b"piece length": 16_384,
        b"pieces": b"p" * 20,
    }
    metainfo: dict[bytes, object] = {b"announce": tracker, b"info": info}
    if announce_list is not None:
        metainfo[b"announce-list"] = announce_list
    return bencode(metainfo)


def test_torrent_normalization_replaces_passkeys_and_preserves_info_hash() -> None:
    source = torrent_content(
        announce_list=[
            [b"https://c411.org/anything/old-user-passkey"],
            [b"https://tk.c411.tw/anything/old-user-passkey"],
        ]
    )
    info_raw = bencode(
        {
            b"length": 5,
            b"name": b"Film.mkv",
            b"piece length": 16_384,
            b"pieces": b"p" * 20,
        }
    )

    result = normalize_torrent(
        source,
        passkey="test-passkey-123",
        allowed_tracker_hosts=["c411.org", "tk.c411.tw"],
        max_total_size=1_000,
    )

    assert b"old-user-passkey" not in result.content
    assert result.content.count(b"https://c411.org/announce/test-passkey-123") == 2
    assert b"https://tk.c411.tw/announce/test-passkey-123" in result.content
    assert result.info_hash == hashlib.sha1(info_raw, usedforsecurity=False).hexdigest()
    assert result.name == "Film.mkv"
    assert result.total_size == 5


@pytest.mark.parametrize(
    "content",
    [
        b"not-bencode",
        torrent_content(tracker=b"https://evil.example/anything/old-user-passkey"),
        torrent_content(name=b"../Film.mkv"),
    ],
)
def test_torrent_normalization_rejects_invalid_or_unauthorized_files(content: bytes) -> None:
    with pytest.raises(TorrentValidationError):
        normalize_torrent(
            content,
            passkey="test-passkey-123",
            allowed_tracker_hosts=["c411.org", "tk.c411.tw"],
            max_total_size=1_000,
        )


class FakeMonitor:
    def __init__(self) -> None:
        self.added: list[tuple[bytes, str]] = []
        self.snapshots: list[QBittorrentTorrent] = []
        self.fail_add = False

    async def add_qbittorrent_torrent(
        self,
        content: bytes,
        *,
        save_path: str,
        expected_info_hash: str,
    ) -> None:
        if self.fail_add:
            raise IntegrationRequestError("offline")
        self.added.append((content, save_path))
        assert (
            expected_info_hash
            == hashlib.sha1(
                bencode(
                    {
                        b"length": 5,
                        b"name": b"Film.mkv",
                        b"piece length": 16_384,
                        b"pieces": b"p" * 20,
                    }
                ),
                usedforsecurity=False,
            ).hexdigest()
        )

    async def qbittorrent_torrents_by_hashes(
        self, hashes: list[str]
    ) -> tuple[list[QBittorrentTorrent], bool]:
        return [snapshot for snapshot in self.snapshots if snapshot.id in hashes], False


async def create_user(db: AsyncSession, data_root: Path, username: str) -> User:
    WorkspaceManager(data_root).create(username)
    user = User(username=username, password_hash=hash_password("correct-horse-battery"))
    db.add(user)
    await db.commit()
    return user


async def login(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    token = client.cookies.get("wos_csrf")
    assert token is not None
    return {"X-CSRF-Token": token}


@pytest.mark.asyncio
async def test_upload_uses_server_path_and_user_listing_is_isolated(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    thomas = await create_user(db_session, data_root, "thomas")
    alice = await create_user(db_session, data_root, "alice")
    settings = Settings(
        data_root=data_root,
        c411_passkey=SecretStr("test-passkey-123"),
        c411_tracker_hosts=["c411.org", "tk.c411.tw"],
    )
    app.dependency_overrides[get_settings] = lambda: settings
    monitor = FakeMonitor()
    app.state.external_services_monitor = monitor
    headers = await login(client, "thomas")

    uploaded = await client.post(
        "/api/v1/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )

    assert uploaded.status_code == 201, uploaded.text
    assert monitor.added[0][1] == "/data/thomas/downloads"
    assert b"old-user-passkey" not in monitor.added[0][0]
    torrent_hash = uploaded.json()["id"]
    association = await db_session.scalar(
        select(UserTorrent).where(
            UserTorrent.user_id == thomas.id,
            UserTorrent.info_hash == torrent_hash,
        )
    )
    assert association is not None
    db_session.add(UserTorrent(user_id=alice.id, info_hash="b" * 40, name="Alice.mkv"))
    await db_session.commit()
    monitor.snapshots = [
        QBittorrentTorrent(
            id=torrent_hash,
            name="Film.mkv",
            state="downloading",
            progress=0.5,
            size_bytes=10,
            downloaded_bytes=5,
            uploaded_bytes=0,
            download_speed_bytes=2,
            upload_speed_bytes=0,
            ratio=0,
            eta_seconds=3,
            category=None,
            tracker_host="c411.org",
        )
    ]

    listing = await client.get("/api/v1/torrents")

    assert listing.status_code == 200
    assert [item["id"] for item in listing.json()["torrents"]] == [torrent_hash]
    assert listing.json()["torrents"][0]["state"] == "downloading"
    assert thomas.id != alice.id


@pytest.mark.asyncio
async def test_upload_rejects_oversized_invalid_and_offline_requests(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_user(db_session, data_root, "thomas")
    settings = Settings(
        data_root=data_root,
        c411_passkey=SecretStr("test-passkey-123"),
        c411_tracker_hosts=["c411.org", "tk.c411.tw"],
    )
    app.dependency_overrides[get_settings] = lambda: settings
    monitor = FakeMonitor()
    app.state.external_services_monitor = monitor
    headers = await login(client, "thomas")

    invalid = await client.post(
        "/api/v1/torrents",
        files={"torrent": ("bad.torrent", b"invalid", "application/x-bittorrent")},
        headers=headers,
    )
    oversized = await client.post(
        "/api/v1/torrents",
        files={
            "torrent": (
                "large.torrent",
                b"x" * (4_194_304 + 1),
                "application/x-bittorrent",
            )
        },
        headers=headers,
    )
    monitor.fail_add = True
    offline = await client.post(
        "/api/v1/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )

    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "torrent_invalid"
    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] == "torrent_too_large"
    assert offline.status_code == 503
    assert offline.json()["detail"]["code"] == "qbittorrent_unavailable"
