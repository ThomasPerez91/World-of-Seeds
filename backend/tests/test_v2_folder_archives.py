from __future__ import annotations

import io
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.models import (
    DatabaseOption,
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.options import PostgresOptionsRegistry
from app.storage import SharedContentStore
from app.torrents.downloads import (
    ManagedArchiveBusyError,
    ManagedArchiveEntry,
    ManagedFileDownloader,
    ManagedFolderArchiver,
)

PASSWORD = "correct-horse-battery"


@dataclass(frozen=True, slots=True)
class _RequestRef:
    id: uuid.UUID
    managed_torrent_id: uuid.UUID


async def _login(client: AsyncClient, username: str = "folder-owner") -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200


async def _folder_torrent(
    db: AsyncSession,
    data_root: Path,
) -> tuple[_RequestRef, dict[str, bytes]]:
    contents = {
        "Saison 1/Episode 1.mkv": b"one",
        "Saison 1/Épisode 2 %.mkv": b"two",
        "Saison 10/Episode_1.mkv": b"ten",
        "Extras/Sub/file.txt": b"extra",
        "Bonus %_Été/A.txt": b"a",
        "Docs/read me.txt": b"docs",
        "Musique/song.flac": b"music",
        "Images/poster.jpg": b"image",
        "Sous_titres/fr.srt": b"subs",
    }
    owner = User(username="folder-owner", password_hash=hash_password(PASSWORD))
    torrent = ManagedTorrent(
        info_hash="f" * 40,
        name="Series",
        total_size=sum(map(len, contents.values())),
        state=ManagedTorrentState.READY,
        progress=1,
        ready_at=datetime.now(UTC) - timedelta(hours=1),
        manifest_version=3,
        manifest_checksum="a" * 64,
        manifest_file_count=len(contents),
        manifest_total_size=sum(map(len, contents.values())),
    )
    request = TorrentRequest(
        user=owner,
        managed_torrent=torrent,
        state=TorrentRequestState.READY,
        ready_at=datetime.now(UTC) - timedelta(hours=1),
        unsubscribe_at=datetime.now(UTC) + timedelta(days=2),
    )
    db.add_all([owner, torrent, request])
    for index, (relative_path, content) in enumerate(contents.items()):
        db.add(
            TorrentFile(
                managed_torrent=torrent,
                file_index=index,
                relative_path=relative_path,
                size=len(content),
            )
        )
    await PostgresOptionsRegistry().initialize(db)
    await db.commit()

    SharedContentStore(data_root).prepare(torrent.storage_key)
    root = data_root / "content" / torrent.storage_key.hex
    for relative_path, content in contents.items():
        target = root.joinpath(*relative_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return _RequestRef(request.id, torrent.id), contents


@pytest.mark.asyncio
async def test_directory_listing_uses_manifest_summaries_and_supports_nested_parent(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    request, _ = await _folder_torrent(db_session, data_root)
    await _login(client)

    root = await client.get(f"/api/v2/torrents/{request.id}/download-directories")
    nested = await client.get(
        f"/api/v2/torrents/{request.id}/download-directories",
        params={"parent": "Extras"},
    )

    assert root.status_code == 200
    payload = root.json()
    assert len(payload["snapshot_id"]) == 64
    assert payload["path"] == ""
    assert [item["name"] for item in payload["directories"]] == [
        "Bonus %_Été",
        "Docs",
        "Extras",
        "Images",
        "Musique",
        "Saison 1",
        "Saison 10",
        "Sous_titres",
    ]
    season = next(item for item in payload["directories"] if item["name"] == "Saison 1")
    assert season == {
        "name": "Saison 1",
        "relative_path": "Saison 1",
        "file_count": 2,
        "total_size": 6,
        "archive_available": True,
    }
    assert nested.status_code == 200
    assert nested.json()["directories"] == [
        {
            "name": "Sub",
            "relative_path": "Extras/Sub",
            "file_count": 1,
            "total_size": 5,
            "archive_available": True,
        }
    ]


@pytest.mark.asyncio
async def test_folder_zip_selects_exact_subtree_and_preserves_requested_root(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    request, contents = await _folder_torrent(db_session, data_root)
    await _login(client)
    listing = (await client.get(f"/api/v2/torrents/{request.id}/download-directories")).json()

    response = await client.get(
        f"/api/v2/torrents/{request.id}/download-folder-archive",
        params={"path": "Saison 1", "snapshot": listing["snapshot_id"]},
    )

    assert response.status_code == 200
    assert "Saison%201.zip" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == [
            "Saison 1/Episode 1.mkv",
            "Saison 1/Épisode 2 %.mkv",
        ]
        assert archive.read("Saison 1/Episode 1.mkv") == contents["Saison 1/Episode 1.mkv"]
        assert "Saison 10/Episode_1.mkv" not in archive.namelist()
        assert all(
            archive.getinfo(name).compress_type == zipfile.ZIP_STORED for name in archive.namelist()
        )
    assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0


@pytest.mark.asyncio
async def test_nested_folder_zip_rebases_entries_to_requested_folder(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    request, _ = await _folder_torrent(db_session, data_root)
    await _login(client)
    snapshot = (await client.get(f"/api/v2/torrents/{request.id}/download-directories")).json()[
        "snapshot_id"
    ]

    response = await client.get(
        f"/api/v2/torrents/{request.id}/download-folder-archive",
        params={"path": "Extras/Sub", "snapshot": snapshot},
    )

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["Sub/file.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/Saison 1", "../Saison 1", "./Saison 1", "a\\b", "a//b"])
async def test_folder_archive_rejects_ambiguous_or_traversing_paths(
    path: str,
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    request, _ = await _folder_torrent(db_session, data_root)
    await _login(client)

    response = await client.get(
        f"/api/v2/torrents/{request.id}/download-folder-archive",
        params={"path": path, "snapshot": "0" * 64},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "download_directory_path_invalid"


@pytest.mark.asyncio
async def test_folder_archive_rejects_changed_snapshot_and_missing_folder(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    request, _ = await _folder_torrent(db_session, data_root)
    await _login(client)
    snapshot = (await client.get(f"/api/v2/torrents/{request.id}/download-directories")).json()[
        "snapshot_id"
    ]

    changed = await client.get(
        f"/api/v2/torrents/{request.id}/download-folder-archive",
        params={"path": "Saison 1", "snapshot": "0" * 64},
    )
    missing = await client.get(
        f"/api/v2/torrents/{request.id}/download-folder-archive",
        params={"path": "Saison", "snapshot": snapshot},
    )

    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "download_snapshot_changed"
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "download_directory_not_found"


@pytest.mark.asyncio
async def test_subfolder_limit_is_independent_from_global_archive_availability(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    request, _ = await _folder_torrent(db_session, data_root)
    option = await db_session.get(DatabaseOption, "WOS_FOLDER_ARCHIVE_MAX_BYTES")
    assert option is not None
    option.integer_value = 1_048_576
    large = await db_session.scalar(
        select(TorrentFile).where(TorrentFile.relative_path == "Saison 10/Episode_1.mkv")
    )
    torrent = await db_session.get(ManagedTorrent, request.managed_torrent_id)
    assert large is not None and torrent is not None
    large.size = 1_048_576
    torrent.manifest_total_size = 1_048_576 + 35
    await db_session.commit()
    await _login(client)

    manifest = await client.get(f"/api/v2/torrents/{request.id}/download-manifest")
    directories = await client.get(f"/api/v2/torrents/{request.id}/download-directories")
    season = next(item for item in directories.json()["directories"] if item["name"] == "Saison 1")

    assert manifest.status_code == 200 and manifest.json()["archive_available"] is False
    assert season["archive_available"] is True


@pytest.mark.asyncio
async def test_folder_archive_enforces_subfolder_byte_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    request, _ = await _folder_torrent(db_session, data_root)
    option = await db_session.get(DatabaseOption, "WOS_FOLDER_ARCHIVE_MAX_BYTES")
    assert option is not None
    option.integer_value = 1_048_576
    first = await db_session.scalar(
        select(TorrentFile).where(TorrentFile.relative_path == "Saison 1/Episode 1.mkv")
    )
    assert first is not None
    first.size = 1_048_576
    await db_session.commit()
    await _login(client)
    listing = (await client.get(f"/api/v2/torrents/{request.id}/download-directories")).json()

    response = await client.get(
        f"/api/v2/torrents/{request.id}/download-folder-archive",
        params={"path": "Saison 1", "snapshot": listing["snapshot_id"]},
    )

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "folder_archive_too_large"


@pytest.mark.asyncio
async def test_expired_subscription_cannot_list_or_start_folder_archive(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    request, _ = await _folder_torrent(db_session, data_root)
    stored_request = await db_session.get(TorrentRequest, request.id)
    assert stored_request is not None
    stored_request.unsubscribe_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    await _login(client)

    listing = await client.get(f"/api/v2/torrents/{request.id}/download-directories")
    archive = await client.get(
        f"/api/v2/torrents/{request.id}/download-folder-archive",
        params={"path": "Saison 1", "snapshot": "0" * 64},
    )

    assert listing.status_code == 404
    assert archive.status_code == 404
    assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0


@pytest.mark.asyncio
async def test_another_user_cannot_list_or_archive_owned_folders(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    request, _ = await _folder_torrent(db_session, data_root)
    stranger = User(username="folder-stranger", password_hash=hash_password(PASSWORD))
    db_session.add(stranger)
    await db_session.commit()
    await _login(client, "folder-stranger")

    listing = await client.get(f"/api/v2/torrents/{request.id}/download-directories")
    archive = await client.get(
        f"/api/v2/torrents/{request.id}/download-folder-archive",
        params={"path": "Saison 1", "snapshot": "0" * 64},
    )

    assert listing.status_code == 404
    assert archive.status_code == 404


def test_archive_concurrency_guard_honors_dynamic_global_limit(data_root: Path) -> None:
    store = SharedContentStore(data_root)
    archivers = [
        ManagedFolderArchiver(
            ManagedFileDownloader(store),
            storage_key=uuid.uuid4(),
            entries=(ManagedArchiveEntry("file", 1, 0),),
            manifest_checksum="a" * 64,
            manifest_version=1,
            download_name="folder.zip",
            max_concurrent_global=2,
        )
        for _ in range(3)
    ]
    try:
        archivers[0].acquire()
        archivers[1].acquire()
        with pytest.raises(ManagedArchiveBusyError):
            archivers[2].acquire()
    finally:
        for archiver in archivers:
            archiver.release()
