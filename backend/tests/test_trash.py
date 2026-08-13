import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.files import WorkspaceManager
from app.models import TrashEntry, User
from app.trash import TrashFilesystem, TrashPersistenceError, TrashService


async def create_workspace_user(
    db: AsyncSession,
    data_root: Path,
    *,
    username: str = "thomas",
    password: str = "correct-horse-battery",
) -> User:
    WorkspaceManager(data_root).create(username)
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


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
async def test_file_is_atomically_trashed_listed_and_isolated_by_user(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    user = await create_workspace_user(db_session, data_root)
    user_id = user.id
    await create_workspace_user(
        db_session,
        data_root,
        username="other",
        password="other-password-long",
    )
    movie = workspace(data_root) / "downloads" / "movie.mkv"
    movie.write_bytes(b"video")
    await login(client)

    response = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/movie.mkv"},
        headers=csrf_header(client),
    )

    assert response.status_code == 201
    entry = response.json()
    entry_id = uuid.UUID(entry["id"])
    assert entry["original_path"] == "downloads/movie.mkv"
    assert entry["name"] == "movie.mkv"
    assert entry["kind"] == "file"
    assert entry["size"] == len(b"video")
    assert not movie.exists()
    stored = data_root / ".trash" / str(user_id) / str(entry_id)
    assert stored.read_bytes() == b"video"

    listing = await client.get("/api/v1/trash")
    assert listing.status_code == 200
    assert listing.json()["entries"] == [entry]
    assert listing.json()["truncated"] is False

    client.cookies.clear()
    await login(client, "other", "other-password-long")
    other_listing = await client.get("/api/v1/trash")
    forbidden_restore = await client.post(
        f"/api/v1/trash/{entry_id}/restore",
        headers=csrf_header(client),
    )
    assert other_listing.json()["entries"] == []
    assert forbidden_restore.status_code == 404
    assert stored.exists()


@pytest.mark.asyncio
async def test_restore_survives_username_change_and_never_replaces_a_collision(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    user = await create_workspace_user(db_session, data_root)
    movie = workspace(data_root) / "downloads" / "movie.mkv"
    movie.write_bytes(b"trashed-video")
    await login(client)
    trashed = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/movie.mkv"},
        headers=csrf_header(client),
    )
    entry_id = trashed.json()["id"]

    WorkspaceManager(data_root).rename("thomas", "renamed")
    user.username = "renamed"
    await db_session.commit()
    renamed_movie = workspace(data_root, "renamed") / "downloads" / "movie.mkv"
    renamed_movie.write_bytes(b"collision")

    collision = await client.post(
        f"/api/v1/trash/{entry_id}/restore",
        headers=csrf_header(client),
    )
    assert collision.status_code == 409
    assert renamed_movie.read_bytes() == b"collision"

    renamed_movie.unlink()
    restored = await client.post(
        f"/api/v1/trash/{entry_id}/restore",
        headers=csrf_header(client),
    )
    assert restored.status_code == 200
    assert restored.json() == {
        "path": "downloads/movie.mkv",
        "name": "movie.mkv",
        "kind": "file",
    }
    assert renamed_movie.read_bytes() == b"trashed-video"
    assert await db_session.get(TrashEntry, uuid.UUID(entry_id)) is None
    assert not (data_root / ".trash" / str(user.id) / entry_id).exists()


@pytest.mark.asyncio
async def test_permanent_directory_deletion_never_follows_nested_symlinks(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    tmp_path: Path,
) -> None:
    user = await create_workspace_user(db_session, data_root)
    collection = workspace(data_root) / "downloads" / "collection"
    nested = collection / "nested"
    nested.mkdir(parents=True)
    (nested / "episode.mkv").write_bytes(b"episode")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"must-survive")
    (collection / "outside-link").symlink_to(outside)
    await login(client)
    trashed = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/collection"},
        headers=csrf_header(client),
    )
    entry_id = trashed.json()["id"]

    purged = await client.delete(
        f"/api/v1/trash/{entry_id}",
        headers=csrf_header(client),
    )

    assert purged.status_code == 204
    assert outside.read_bytes() == b"must-survive"
    assert not (data_root / ".trash" / str(user.id) / entry_id).exists()
    assert await db_session.get(TrashEntry, uuid.UUID(entry_id)) is None


@pytest.mark.asyncio
async def test_trash_database_failure_restores_the_source(
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_workspace_user(db_session, data_root)
    user_id = user.id
    movie = workspace(data_root) / "downloads" / "movie.mkv"
    movie.write_bytes(b"video")
    service = TrashService(
        db_session,
        TrashFilesystem(data_root, WorkspaceManager(data_root)),
    )

    async def failing_commit() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_session, "commit", failing_commit)
    with pytest.raises(TrashPersistenceError):
        await service.move_to_trash(user, "downloads/movie.mkv")

    assert movie.read_bytes() == b"video"
    trash_user = data_root / ".trash" / str(user_id)
    assert list(trash_user.iterdir()) == []
    assert (await db_session.scalars(select(TrashEntry))).all() == []


@pytest.mark.asyncio
async def test_restore_database_failure_returns_the_entry_to_trash(
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_workspace_user(db_session, data_root)
    movie = workspace(data_root) / "downloads" / "movie.mkv"
    movie.write_bytes(b"video")
    filesystem = TrashFilesystem(data_root, WorkspaceManager(data_root))
    service = TrashService(db_session, filesystem)
    record = await service.move_to_trash(user, "downloads/movie.mkv")
    user_id = user.id
    record_id = record.id
    original_commit = db_session.commit

    async def failing_commit() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_session, "commit", failing_commit)
    with pytest.raises(TrashPersistenceError):
        await service.restore(user, record_id)

    assert not movie.exists()
    assert (data_root / ".trash" / str(user_id) / str(record_id)).read_bytes() == b"video"
    monkeypatch.setattr(db_session, "commit", original_commit)
    assert await db_session.get(TrashEntry, record_id) is not None


@pytest.mark.asyncio
async def test_purge_is_safe_to_retry_when_metadata_commit_fails(
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await create_workspace_user(db_session, data_root)
    movie = workspace(data_root) / "downloads" / "movie.mkv"
    movie.write_bytes(b"video")
    service = TrashService(
        db_session,
        TrashFilesystem(data_root, WorkspaceManager(data_root)),
    )
    record = await service.move_to_trash(user, "downloads/movie.mkv")
    user_id = user.id
    record_id = record.id
    original_commit = db_session.commit

    async def failing_commit() -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db_session, "commit", failing_commit)
    with pytest.raises(TrashPersistenceError):
        await service.purge(user_id, record_id)
    assert not (data_root / ".trash" / str(user_id) / str(record_id)).exists()

    monkeypatch.setattr(db_session, "commit", original_commit)
    await service.purge(user_id, record_id)
    assert await db_session.get(TrashEntry, record_id) is None


@pytest.mark.asyncio
async def test_trash_requires_authentication_csrf_and_safe_mutable_paths(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    tmp_path: Path,
) -> None:
    anonymous = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/movie.mkv"},
    )
    assert anonymous.status_code == 401

    await create_workspace_user(db_session, data_root)
    downloads = workspace(data_root) / "downloads"
    (downloads / "movie.mkv").write_bytes(b"video")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    (downloads / "escape").symlink_to(outside)
    await login(client)

    missing_csrf = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/movie.mkv"},
    )
    traversal = await client.post(
        "/api/v1/trash",
        json={"path": "../outside.txt"},
        headers=csrf_header(client),
    )
    protected = await client.post(
        "/api/v1/trash",
        json={"path": "downloads"},
        headers=csrf_header(client),
    )
    symlink = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/escape"},
        headers=csrf_header(client),
    )

    assert missing_csrf.status_code == 403
    assert traversal.status_code == 400
    assert protected.status_code == 403
    assert symlink.status_code == 403
    assert (downloads / "movie.mkv").read_bytes() == b"video"
    assert outside.read_bytes() == b"outside"
