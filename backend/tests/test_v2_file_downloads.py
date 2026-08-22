from __future__ import annotations

import io
import os
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.models import (
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
    DownloadConcurrencyError,
    DownloadLeaseManager,
    DownloadRateLimiter,
    ManagedDownloadError,
)

PASSWORD = "correct-horse-battery"
CONTENT = b"World of Seeds V2 download"


async def _login(client: AsyncClient, username: str = "thomas") -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
    )
    assert response.status_code == 200


async def _ready_file(
    db: AsyncSession,
    data_root: Path,
    *,
    content: bytes = CONTENT,
) -> tuple[User, ManagedTorrent, TorrentRequest, TorrentFile, Path]:
    owner = User(username="thomas", password_hash=hash_password(PASSWORD))
    torrent = ManagedTorrent(
        info_hash="d" * 40,
        name="Example",
        total_size=len(content),
        state=ManagedTorrentState.READY,
        progress=1.0,
        manifest_version=1,
        manifest_checksum="e" * 64,
        manifest_file_count=1,
        manifest_total_size=len(content),
    )
    request = TorrentRequest(
        user=owner,
        managed_torrent=torrent,
        state=TorrentRequestState.READY,
    )
    torrent_file = TorrentFile(
        managed_torrent=torrent,
        file_index=0,
        relative_path="folder/seed.txt",
        size=len(content),
    )
    db.add_all([owner, torrent, request, torrent_file])
    await PostgresOptionsRegistry().initialize(db)
    await db.commit()

    SharedContentStore(data_root).prepare(torrent.storage_key)
    physical_file = data_root / "content" / torrent.storage_key.hex / "folder" / "seed.txt"
    physical_file.parent.mkdir()
    physical_file.write_bytes(content)
    return owner, torrent, request, torrent_file, physical_file


def _url(request: TorrentRequest, torrent_file: TorrentFile) -> str:
    return f"/api/v2/torrents/{request.id}/files/{torrent_file.id}/download"


@pytest.mark.asyncio
async def test_owned_ready_file_supports_head_range_etag_and_releases_lease(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    _, _, request, torrent_file, _ = await _ready_file(db_session, data_root)
    await _login(client)
    url = _url(request, torrent_file)

    head = await client.head(url)
    full = await client.get(url)
    partial = await client.get(url, headers={"Range": "bytes=6-13"})
    ignored_range = await client.get(
        url,
        headers={"Range": "bytes=0-4", "If-Range": '"obsolete"'},
    )

    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(CONTENT))
    assert full.status_code == 200
    assert full.content == CONTENT
    assert full.headers["etag"] == head.headers["etag"]
    assert full.headers["x-wos-manifest-version"] == "1"
    assert partial.status_code == 206
    assert partial.content == CONTENT[6:14]
    assert partial.headers["content-range"] == f"bytes 6-13/{len(CONTENT)}"
    assert ignored_range.status_code == 200
    assert ignored_range.content == CONTENT
    assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0


@pytest.mark.asyncio
async def test_download_hides_other_owners_and_non_ready_content(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    _, torrent, request, torrent_file, _ = await _ready_file(db_session, data_root)
    torrent_id = torrent.id
    url = _url(request, torrent_file)
    other = User(username="alice", password_hash=hash_password(PASSWORD))
    db_session.add(other)
    await db_session.commit()
    await _login(client, "alice")

    hidden = await client.get(url)
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "torrent_file_not_found"

    await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": client.cookies["wos_csrf"]})
    await _login(client)
    reloaded_torrent = await db_session.get(ManagedTorrent, torrent_id)
    assert reloaded_torrent is not None
    reloaded_torrent.state = ManagedTorrentState.DOWNLOADING
    await db_session.commit()
    unavailable = await client.get(url)
    assert unavailable.status_code == 404


@pytest.mark.asyncio
async def test_changed_file_and_invalid_range_fail_closed_and_release_lease(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    _, _, request, torrent_file, physical_file = await _ready_file(db_session, data_root)
    await _login(client)
    url = _url(request, torrent_file)

    invalid_range = await client.get(url, headers={"Range": "bytes=999-1000"})
    assert invalid_range.status_code == 416
    assert invalid_range.headers["content-range"] == f"bytes */{len(CONTENT)}"
    assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0

    physical_file.write_bytes(b"changed")
    changed = await client.get(url)
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "torrent_file_changed"
    assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0


@pytest.mark.asyncio
async def test_symlinked_manifest_file_is_rejected(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    tmp_path: Path,
) -> None:
    _, _, request, torrent_file, physical_file = await _ready_file(db_session, data_root)
    target = tmp_path / "outside.txt"
    target.write_bytes(CONTENT)
    physical_file.unlink()
    os.symlink(target, physical_file)
    await _login(client)

    response = await client.get(_url(request, torrent_file))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "torrent_file_changed"
    assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0


@pytest.mark.asyncio
async def test_download_lease_limit_reclaims_expired_entries(
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    owner, torrent, request, torrent_file, _ = await _ready_file(db_session, data_root)
    owner_id = owner.id
    torrent_id = torrent.id
    request_id = request.id
    torrent_file_id = torrent_file.id
    now = datetime(2026, 8, 22, tzinfo=UTC)
    manager = DownloadLeaseManager(db_session, lease_seconds=60, clock=lambda: now)

    lease = await manager.acquire(
        user_id=owner_id,
        managed_torrent_id=torrent_id,
        torrent_request_id=request_id,
        torrent_file_id=torrent_file_id,
        max_concurrent=1,
    )
    with pytest.raises(DownloadConcurrencyError):
        await manager.acquire(
            user_id=owner_id,
            managed_torrent_id=torrent_id,
            torrent_request_id=request_id,
            torrent_file_id=torrent_file_id,
            max_concurrent=1,
        )

    lease.expires_at = now - timedelta(seconds=1)
    await db_session.commit()
    replacement = await manager.acquire(
        user_id=owner_id,
        managed_torrent_id=torrent_id,
        torrent_request_id=request_id,
        torrent_file_id=torrent_file_id,
        max_concurrent=1,
    )
    assert replacement.id != lease.id


@pytest.mark.asyncio
async def test_download_lease_cannot_renew_after_lifecycle_revocation(
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    owner, torrent, request, torrent_file, _ = await _ready_file(db_session, data_root)
    now = datetime(2026, 8, 22, tzinfo=UTC)
    manager = DownloadLeaseManager(db_session, lease_seconds=60, clock=lambda: now)
    lease = await manager.acquire(
        user_id=owner.id,
        managed_torrent_id=torrent.id,
        torrent_request_id=request.id,
        torrent_file_id=torrent_file.id,
        max_concurrent=1,
    )
    torrent.state = ManagedTorrentState.PURGING
    torrent.purge_after = now
    request.state = TorrentRequestState.CANCELLED
    await db_session.commit()

    with pytest.raises(ManagedDownloadError):
        await manager.renew(lease.id)


@pytest.mark.asyncio
async def test_rate_limiter_reserves_user_and_global_capacity_from_same_deadline() -> None:
    now = 100.0
    limiter = DownloadRateLimiter(clock=lambda: now)
    first_user = uuid.uuid4()
    second_user = uuid.uuid4()

    assert (
        await limiter.reserve(
            first_user,
            100,
            per_user_bytes_per_second=100,
            global_bytes_per_second=100,
        )
        == 0
    )
    assert await limiter.reserve(
        second_user,
        100,
        per_user_bytes_per_second=100,
        global_bytes_per_second=100,
    ) == pytest.approx(1.0)
    assert await limiter.reserve(
        first_user,
        100,
        per_user_bytes_per_second=100,
        global_bytes_per_second=100,
    ) == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_download_manifest_is_owned_paginated_and_stable(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    _, _, request, torrent_file, _ = await _ready_file(db_session, data_root)
    torrent_file_id = torrent_file.id
    await _login(client)
    base = f"/api/v2/torrents/{request.id}/download-manifest"

    first = await client.get(base, params={"offset": 0, "limit": 1})
    assert first.status_code == 200
    manifest = first.json()
    assert manifest["manifest_version"] == 1
    assert manifest["file_count"] == 1
    assert manifest["total_size"] == len(CONTENT)
    assert manifest["archive_available"] is True
    assert manifest["items"] == [
        {
            "id": str(torrent_file_id),
            "file_index": 0,
            "relative_path": "folder/seed.txt",
            "size": len(CONTENT),
        }
    ]
    assert len(manifest["snapshot_id"]) == 64

    replay = await client.get(base, params={"snapshot": manifest["snapshot_id"]})
    assert replay.status_code == 200
    assert replay.json()["snapshot_id"] == manifest["snapshot_id"]


@pytest.mark.asyncio
async def test_snapshot_change_is_rejected_by_manifest_and_file_endpoints(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    _, _, request, torrent_file, _ = await _ready_file(db_session, data_root)
    file_url = _url(request, torrent_file)
    await _login(client)
    manifest_url = f"/api/v2/torrents/{request.id}/download-manifest"

    changed_manifest = await client.get(manifest_url, params={"snapshot": "0" * 64})
    changed_file = await client.get(
        file_url,
        headers={"X-WOS-Download-Snapshot": "0" * 64},
    )

    assert changed_manifest.status_code == 409
    assert changed_manifest.json()["detail"]["code"] == "download_snapshot_changed"
    assert changed_file.status_code == 409
    assert changed_file.json()["detail"]["code"] == "download_snapshot_changed"
    assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0


@pytest.mark.asyncio
async def test_compatible_fallback_exposes_individual_file_and_streamed_stored_zip(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    _, _, request, torrent_file, _ = await _ready_file(db_session, data_root)
    file_url = _url(request, torrent_file)
    manifest_url = f"/api/v2/torrents/{request.id}/download-manifest"
    archive_url = f"/api/v2/torrents/{request.id}/download-archive"
    await _login(client)
    manifest = (await client.get(manifest_url)).json()
    snapshot = manifest["snapshot_id"]

    individual = await client.get(file_url, params={"snapshot": snapshot})
    archive = await client.get(
        archive_url,
        params={"snapshot": snapshot},
    )

    assert individual.status_code == 200
    assert individual.content == CONTENT
    assert archive.status_code == 200
    assert archive.headers["content-type"].startswith("application/zip")
    assert ".zip" in archive.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(archive.content)) as opened:
        assert opened.namelist() == ["folder/seed.txt"]
        assert opened.read("folder/seed.txt") == CONTENT
        assert opened.getinfo("folder/seed.txt").compress_type == zipfile.ZIP_STORED
    assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0
