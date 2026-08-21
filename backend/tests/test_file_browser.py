import os
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.files import BrowserPathBlockedError, WorkspaceManager
from app.files.browser import InvalidRelativePathError, RelativePath, SandboxedFileBrowser
from app.models import User


async def create_workspace_user(
    db: AsyncSession,
    data_root: Path,
    *,
    username: str = "thomas",
    password: str = "correct-horse-battery",
    must_change_credentials: bool = False,
) -> User:
    WorkspaceManager(data_root).create(username)
    user = User(
        username=username,
        password_hash=hash_password(password),
        must_change_credentials=must_change_credentials,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def login(client: AsyncClient, username: str, password: str) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_user_lists_root_and_nested_directory_metadata(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = data_root / "thomas" / "downloads"
    folder = downloads / "Movies"
    folder.mkdir()
    movie = downloads / "movie.mkv"
    movie.write_bytes(b"video-content")
    note = downloads / "notes.txt"
    note.write_text("hello", encoding="utf-8")
    await login(client, "thomas", "correct-horse-battery")

    root_response = await client.get("/api/v1/files")
    assert root_response.status_code == 200
    root = root_response.json()
    assert root["path"] == ""
    assert root["breadcrumbs"] == [{"label": "Mes fichiers", "path": ""}]
    assert [entry["name"] for entry in root["entries"]] == ["downloads"]
    assert all(entry["kind"] == "directory" for entry in root["entries"])
    assert root["entries"][0]["size"] is None
    assert root["storage"]["total"] > 0
    assert root["storage"]["used"] >= 0
    assert root["storage"]["available"] > 0
    assert root["truncated"] is False
    assert db_session.in_transaction() is False

    nested_response = await client.get("/api/v1/files", params={"path": "downloads"})
    assert nested_response.status_code == 200
    nested = nested_response.json()
    assert nested["breadcrumbs"] == [
        {"label": "Mes fichiers", "path": ""},
        {"label": "downloads", "path": "downloads"},
    ]
    assert [entry["name"] for entry in nested["entries"]] == [
        "Movies",
        "movie.mkv",
        "notes.txt",
    ]
    movie_entry = next(entry for entry in nested["entries"] if entry["name"] == "movie.mkv")
    folder_entry = next(entry for entry in nested["entries"] if entry["name"] == "Movies")
    assert folder_entry["size"] is None
    assert movie_entry["path"] == "downloads/movie.mkv"
    assert movie_entry["kind"] == "file"
    assert movie_entry["size"] == len(b"video-content")
    assert movie_entry["media_type"] in {"video/x-matroska", "video/matroska"}
    assert movie_entry["blocked"] is False
    assert movie_entry["modified_at"].endswith("Z")


@pytest.mark.asyncio
async def test_file_listing_applies_the_dynamic_options_limit(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = data_root / "thomas" / "downloads"
    for index in range(101):
        (downloads / f"file-{index:03}.txt").touch()
    control = data_root / ".wos-control"
    control.mkdir(mode=0o700)
    options = control / ".options"
    options.write_text("WOS_FILES_LIST_MAX_ENTRIES=100\n", encoding="utf-8")
    options.chmod(0o600)
    await login(client, "thomas", "correct-horse-battery")

    response = await client.get("/api/v1/files", params={"path": "downloads"})

    assert response.status_code == 200
    assert len(response.json()["entries"]) == 100
    assert response.json()["truncated"] is True


@pytest.mark.asyncio
async def test_retired_watch_directory_is_hidden_and_cannot_be_opened(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    watch = data_root / "thomas" / "watch"
    watch.mkdir()
    (watch / "legacy.torrent").write_text("legacy", encoding="utf-8")
    await login(client, "thomas", "correct-horse-battery")

    listing = await client.get("/api/v1/files")
    blocked = await client.get("/api/v1/files", params={"path": "watch"})
    blocked_download = await client.get(
        "/api/v1/files/download",
        params={"path": "watch/legacy.torrent"},
    )

    assert [entry["name"] for entry in listing.json()["entries"]] == ["downloads"]
    assert blocked.status_code == 403
    assert blocked_download.status_code == 403
    assert "legacy.torrent" not in blocked.text


@pytest.mark.asyncio
async def test_symlink_is_visible_but_cannot_be_opened(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    tmp_path: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = data_root / "thomas" / "downloads"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (downloads / "escape").symlink_to(outside, target_is_directory=True)
    await login(client, "thomas", "correct-horse-battery")

    listing = await client.get("/api/v1/files", params={"path": "downloads"})
    assert listing.status_code == 200
    link = next(entry for entry in listing.json()["entries"] if entry["name"] == "escape")
    assert link["kind"] == "symlink"
    assert link["blocked"] is True
    assert link["size"] is None

    blocked = await client.get("/api/v1/files", params={"path": "downloads/escape"})
    assert blocked.status_code == 403
    assert "secret.txt" not in blocked.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../other",
        "downloads/../../other",
        "/etc",
        "downloads//nested",
        ".",
        "downloads\\..\\other",
    ],
)
async def test_navigation_rejects_unsafe_paths(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    unsafe_path: str,
) -> None:
    await create_workspace_user(db_session, data_root)
    await login(client, "thomas", "correct-horse-battery")

    response = await client.get("/api/v1/files", params={"path": unsafe_path})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid relative path"


@pytest.mark.asyncio
async def test_navigation_cannot_cross_into_another_user_workspace(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root, username="thomas")
    WorkspaceManager(data_root).create("other")
    secret = data_root / "other" / "downloads" / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    await login(client, "thomas", "correct-horse-battery")

    response = await client.get("/api/v1/files", params={"path": "../other/downloads"})

    assert response.status_code == 400
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_missing_and_regular_file_paths_are_not_listed_as_directories(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = data_root / "thomas" / "downloads"
    (downloads / "movie.mkv").write_bytes(b"video")
    await login(client, "thomas", "correct-horse-battery")

    missing = await client.get("/api/v1/files", params={"path": "downloads/missing"})
    regular_file = await client.get("/api/v1/files", params={"path": "downloads/movie.mkv"})

    assert missing.status_code == 404
    assert regular_file.status_code == 400


@pytest.mark.asyncio
async def test_navigation_requires_current_credentials(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    anonymous = await client.get("/api/v1/files")
    assert anonymous.status_code == 401

    await create_workspace_user(
        db_session,
        data_root,
        username="temporary",
        must_change_credentials=True,
    )
    await login(client, "temporary", "correct-horse-battery")

    forced_change = await client.get("/api/v1/files")
    assert forced_change.status_code == 403


@pytest.mark.parametrize(
    "raw_path",
    ["bad\0name", "a/./b", "a/../b", "a//b", "\\\\server\\share", "é" * 256],
)
def test_relative_path_parser_rejects_ambiguous_components(raw_path: str) -> None:
    with pytest.raises(InvalidRelativePathError):
        RelativePath.parse(raw_path)


def test_storage_metadata_errors_are_reported_as_blocked_paths(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = WorkspaceManager(data_root)
    manager.create("thomas")

    def unavailable_storage(_: int) -> os.statvfs_result:
        raise PermissionError("storage metadata denied")

    monkeypatch.setattr(os, "fstatvfs", unavailable_storage)

    with pytest.raises(BrowserPathBlockedError):
        SandboxedFileBrowser(manager).list_directory("thomas", "")


def test_directory_listing_never_opens_or_scans_nested_directories(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = WorkspaceManager(data_root)
    manager.create("thomas")
    downloads = data_root / "thomas" / "downloads"
    folder = downloads / "collection"
    folder.mkdir()
    (folder / "large.bin").write_bytes(b"data")
    original_open = os.open

    def reject_nested_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == "collection":
            raise AssertionError("directory listing opened a child directory")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", reject_nested_open)

    listing = SandboxedFileBrowser(manager).list_directory("thomas", "downloads")

    collection = next(entry for entry in listing.entries if entry.name == "collection")
    assert collection.size is None
