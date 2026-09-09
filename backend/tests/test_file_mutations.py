import os
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.files.mutations as mutations_module
from app.auth.security import hash_password
from app.files import BrowserPathBlockedError, SandboxedFileMutator, WorkspaceManager
from app.models import User


async def create_workspace_user(
    db: AsyncSession,
    data_root: Path,
    *,
    username: str = "thomas",
    password: str = "correct-horse-battery",
    must_change_credentials: bool = False,
) -> None:
    WorkspaceManager(data_root).create(username)
    db.add(
        User(
            username=username,
            password_hash=hash_password(password),
            must_change_credentials=must_change_credentials,
        )
    )
    await db.commit()


async def login(
    client: AsyncClient,
    username: str = "thomas",
    password: str = "correct-horse-battery",
) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def csrf_header(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get("wos_csrf")
    assert token is not None
    return {"X-CSRF-Token": token}


def workspace(data_root: Path, username: str = "thomas") -> Path:
    return data_root / username


@pytest.mark.asyncio
async def test_files_and_directories_are_renamed_without_loading_their_contents(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = workspace(data_root) / "downloads"
    movie = downloads / "movie.mkv"
    movie.write_bytes(b"video-content")
    collection = downloads / "collection"
    collection.mkdir()
    (collection / "episode.mkv").write_bytes(b"episode")
    await login(client)

    renamed_file = await client.patch(
        "/api/v1/files/rename",
        json={"path": "downloads/movie.mkv", "basename": "film"},
        headers=csrf_header(client),
    )
    renamed_directory = await client.patch(
        "/api/v1/files/rename",
        json={"path": "downloads/collection", "basename": "series"},
        headers=csrf_header(client),
    )

    assert renamed_file.status_code == 200
    assert renamed_file.json() == {
        "path": "downloads/film.mkv",
        "name": "film.mkv",
        "kind": "file",
    }
    assert not movie.exists()
    assert (downloads / "film.mkv").read_bytes() == b"video-content"
    assert renamed_directory.status_code == 200
    assert renamed_directory.json()["path"] == "downloads/series"
    assert (downloads / "series" / "episode.mkv").read_bytes() == b"episode"


@pytest.mark.asyncio
async def test_single_directory_creation_is_sandboxed_and_reports_collisions(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    await login(client)
    headers = csrf_header(client)

    created = await client.post(
        "/api/v1/files/directory",
        json={"parent": "downloads", "name": "Nouveau dossier"},
        headers=headers,
    )
    collision = await client.post(
        "/api/v1/files/directory",
        json={"parent": "downloads", "name": "Nouveau dossier"},
        headers=headers,
    )
    traversal = await client.post(
        "/api/v1/files/directory",
        json={"parent": "../other", "name": "escape"},
        headers=headers,
    )

    assert created.status_code == 201
    assert created.json()["path"] == "downloads/Nouveau dossier"
    assert (workspace(data_root) / "downloads" / "Nouveau dossier").is_dir()
    assert collision.status_code == 409
    assert traversal.status_code == 400


@pytest.mark.asyncio
async def test_file_rename_preserves_simple_compound_missing_and_hidden_extensions(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = workspace(data_root) / "downloads"
    for name in ("film.mkv", "archive.tar.gz", "README", ".hiddenfile"):
        (downloads / name).write_bytes(name.encode())
    await login(client)
    headers = csrf_header(client)

    cases = [
        ("film.mkv", "Mon film", "Mon film.mkv"),
        ("archive.tar.gz", "sauvegarde", "sauvegarde.tar.gz"),
        ("README", "LISEZMOI", "LISEZMOI"),
        (".hiddenfile", "secret", "secret"),
    ]
    for source, basename, expected in cases:
        response = await client.patch(
            "/api/v1/files/rename",
            json={"path": f"downloads/{source}", "basename": basename},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["name"] == expected
        assert (downloads / expected).is_file()


@pytest.mark.asyncio
async def test_file_rename_cannot_change_or_remove_extension(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = workspace(data_root) / "downloads"
    (downloads / "film.mkv").write_bytes(b"video")
    await login(client)

    response = await client.patch(
        "/api/v1/files/rename",
        json={"path": "downloads/film.mkv", "basename": "film.exe"},
        headers=csrf_header(client),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "film.exe.mkv"
    assert not (downloads / "film.exe").exists()
    assert (downloads / "film.exe.mkv").read_bytes() == b"video"


@pytest.mark.asyncio
async def test_files_and_directories_move_between_sandboxed_directories(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = workspace(data_root) / "downloads"
    archive = downloads / "archive"
    archive.mkdir()
    library = downloads / "library"
    library.mkdir()
    (downloads / "movie.mkv").write_bytes(b"video")
    series = downloads / "series"
    series.mkdir()
    (series / "episode.mkv").write_bytes(b"episode")
    await login(client)

    moved_file = await client.post(
        "/api/v1/files/move",
        json={
            "path": "downloads/movie.mkv",
            "destination_directory": "downloads/archive",
        },
        headers=csrf_header(client),
    )
    moved_directory = await client.post(
        "/api/v1/files/move",
        json={"path": "downloads/series", "destination_directory": "downloads/library"},
        headers=csrf_header(client),
    )

    assert moved_file.status_code == 200
    assert moved_file.json()["path"] == "downloads/archive/movie.mkv"
    assert (archive / "movie.mkv").read_bytes() == b"video"
    assert moved_directory.status_code == 200
    assert moved_directory.json()["kind"] == "directory"
    assert (library / "series" / "episode.mkv").read_bytes() == b"episode"
    assert not (downloads / "series").exists()


@pytest.mark.asyncio
async def test_mutations_never_replace_an_existing_or_concurrent_destination(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = workspace(data_root) / "downloads"
    target = downloads / "target"
    target.mkdir()
    (downloads / "movie.mkv").write_bytes(b"source")
    (target / "movie.mkv").write_bytes(b"existing")
    await login(client)

    collision = await client.post(
        "/api/v1/files/move",
        json={"path": "downloads/movie.mkv", "destination_directory": "downloads/target"},
        headers=csrf_header(client),
    )

    assert collision.status_code == 409
    assert (downloads / "movie.mkv").read_bytes() == b"source"
    assert (target / "movie.mkv").read_bytes() == b"existing"

    (target / "movie.mkv").unlink()
    original_rename = mutations_module.rename_without_replacement

    def create_destination_then_rename(
        source: str,
        destination: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
    ) -> None:
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o640,
            dir_fd=destination_directory_fd,
        )
        os.write(destination_fd, b"concurrent")
        os.close(destination_fd)
        original_rename(
            source,
            destination,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
        )

    monkeypatch.setattr(
        mutations_module,
        "rename_without_replacement",
        create_destination_then_rename,
    )
    concurrent = await client.post(
        "/api/v1/files/move",
        json={"path": "downloads/movie.mkv", "destination_directory": "downloads/target"},
        headers=csrf_header(client),
    )

    assert concurrent.status_code == 409
    assert (downloads / "movie.mkv").read_bytes() == b"source"
    assert (target / "movie.mkv").read_bytes() == b"concurrent"


@pytest.mark.asyncio
async def test_mutations_reject_traversal_symlinks_and_protected_roots(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    tmp_path: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    downloads = workspace(data_root) / "downloads"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (downloads / "escape").symlink_to(outside)
    await login(client)
    headers = csrf_header(client)

    traversal = await client.patch(
        "/api/v1/files/rename",
        json={"path": "../outside.txt", "basename": "stolen"},
        headers=headers,
    )
    symlink = await client.patch(
        "/api/v1/files/rename",
        json={"path": "downloads/escape", "basename": "renamed"},
        headers=headers,
    )
    root = await client.patch(
        "/api/v1/files/rename",
        json={"path": "", "basename": "renamed"},
        headers=headers,
    )
    downloads_root = await client.post(
        "/api/v1/files/move",
        json={"path": "downloads", "destination_directory": ""},
        headers=headers,
    )
    control_character = await client.patch(
        "/api/v1/files/rename",
        json={"path": "downloads/escape", "basename": "bad\nname"},
        headers=headers,
    )

    assert traversal.status_code == 400
    assert symlink.status_code == 403
    assert root.status_code == 403
    assert downloads_root.status_code == 403
    assert control_character.status_code == 400
    assert outside.read_text(encoding="utf-8") == "secret"
    assert (downloads / "escape").is_symlink()


@pytest.mark.asyncio
async def test_directory_cannot_be_moved_into_itself_or_a_descendant(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    folder = workspace(data_root) / "downloads" / "folder"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    await login(client)

    response = await client.post(
        "/api/v1/files/move",
        json={
            "path": "downloads/folder",
            "destination_directory": "downloads/folder/nested",
        },
        headers=csrf_header(client),
    )

    assert response.status_code == 400
    assert folder.is_dir()
    assert nested.is_dir()


def test_source_swapped_during_mutation_is_restored_without_following_symlink(
    data_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    WorkspaceManager(data_root).create("thomas")
    downloads = workspace(data_root) / "downloads"
    target = downloads / "target"
    target.mkdir()
    source = downloads / "movie.mkv"
    source.write_bytes(b"original")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    original_rename = mutations_module.rename_without_replacement
    calls = 0

    def swap_source_then_rename(
        source_name: str,
        destination_name: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            os.rename(
                source_name,
                "original-away.mkv",
                src_dir_fd=source_directory_fd,
                dst_dir_fd=source_directory_fd,
            )
            os.symlink(outside, source_name, dir_fd=source_directory_fd)
        original_rename(
            source_name,
            destination_name,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
        )

    monkeypatch.setattr(
        mutations_module,
        "rename_without_replacement",
        swap_source_then_rename,
    )

    with pytest.raises(BrowserPathBlockedError):
        SandboxedFileMutator(WorkspaceManager(data_root)).move(
            "thomas",
            "downloads/movie.mkv",
            "downloads/target",
        )

    assert calls == 2
    assert source.is_symlink()
    assert not (target / "movie.mkv").exists()
    assert (downloads / "original-away.mkv").read_bytes() == b"original"
    assert outside.read_bytes() == b"secret"


@pytest.mark.asyncio
async def test_mutations_require_authentication_current_credentials_and_csrf(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    anonymous = await client.patch(
        "/api/v1/files/rename",
        json={"path": "downloads/movie.mkv", "basename": "film"},
    )
    assert anonymous.status_code == 401

    await create_workspace_user(
        db_session,
        data_root,
        username="temporary",
        must_change_credentials=True,
    )
    await login(client, "temporary")
    forced_change = await client.patch(
        "/api/v1/files/rename",
        json={"path": "downloads/movie.mkv", "basename": "film"},
        headers=csrf_header(client),
    )
    assert forced_change.status_code == 403

    client.cookies.clear()
    await create_workspace_user(db_session, data_root, username="active")
    await login(client, "active")
    missing_csrf = await client.patch(
        "/api/v1/files/rename",
        json={"path": "downloads/movie.mkv", "basename": "film"},
    )
    assert missing_csrf.status_code == 403
