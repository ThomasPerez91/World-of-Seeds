import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.files import WorkspaceManager
from app.models import TrashEntry, User


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    is_admin: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=is_admin,
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def create_workspace_user(
    db: AsyncSession,
    data_root: Path,
    *,
    username: str,
    password: str,
) -> User:
    WorkspaceManager(data_root).create(username)
    return await create_user(db, username=username, password=password)


async def login(client: AsyncClient, username: str, password: str) -> None:
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def csrf_header(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get("wos_csrf")
    assert token is not None
    return {"X-CSRF-Token": token}


@pytest.mark.asyncio
async def test_storage_overview_is_global_and_admin_only(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_user(
        db_session,
        username="admin",
        password="admin-password-long",
        is_admin=True,
    )
    await create_user(
        db_session,
        username="active-user",
        password="active-password-long",
    )
    await create_user(
        db_session,
        username="suspended-user",
        password="suspended-password-long",
        is_active=False,
    )

    await login(client, "active-user", "active-password-long")
    forbidden = await client.get("/api/v1/admin/storage")
    assert forbidden.status_code == 403

    await login(client, "admin", "admin-password-long")
    response = await client.get("/api/v1/admin/storage")

    assert response.status_code == 200
    overview = response.json()
    assert overview["total"] > 0
    assert overview["used"] >= 0
    assert overview["available"] > 0
    assert overview["active_users"] == 2
    assert overview["suspended_users"] == 1
    assert overview["trash_entries"] == 0
    assert overview["known_trash_bytes"] == 0


@pytest.mark.asyncio
async def test_admin_lists_and_purges_trash_across_users(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_user(
        db_session,
        username="admin",
        password="admin-password-long",
        is_admin=True,
    )
    first = await create_workspace_user(
        db_session,
        data_root,
        username="first-user",
        password="first-password-long",
    )
    second = await create_workspace_user(
        db_session,
        data_root,
        username="second-user",
        password="second-password-long",
    )
    (data_root / first.username / "downloads" / "first.mkv").write_bytes(b"first")
    (data_root / second.username / "downloads" / "second.mkv").write_bytes(b"second")

    await login(client, first.username, "first-password-long")
    first_response = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/first.mkv"},
        headers=csrf_header(client),
    )
    assert first_response.status_code == 201
    first_entry_id = uuid.UUID(first_response.json()["id"])

    await login(client, second.username, "second-password-long")
    second_response = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/second.mkv"},
        headers=csrf_header(client),
    )
    assert second_response.status_code == 201
    second_entry_id = uuid.UUID(second_response.json()["id"])

    forbidden = await client.get("/api/v1/admin/trash")
    assert forbidden.status_code == 403

    await login(client, "admin", "admin-password-long")
    listing = await client.get("/api/v1/admin/trash")
    assert listing.status_code == 200
    assert listing.json()["truncated"] is False
    assert {item["username"] for item in listing.json()["entries"]} == {
        "first-user",
        "second-user",
    }

    missing_csrf = await client.delete(f"/api/v1/admin/trash/{first_entry_id}")
    assert missing_csrf.status_code == 403
    purged_one = await client.delete(
        f"/api/v1/admin/trash/{first_entry_id}",
        headers=csrf_header(client),
    )
    assert purged_one.status_code == 204
    assert await db_session.get(TrashEntry, first_entry_id) is None
    assert await db_session.get(TrashEntry, second_entry_id) is not None

    purged_all = await client.delete(
        "/api/v1/admin/trash",
        headers=csrf_header(client),
    )
    assert purged_all.status_code == 200
    assert purged_all.json() == {"purged": 1, "remaining": 0}
    remaining = await db_session.scalar(select(func.count()).select_from(TrashEntry))
    assert remaining == 0
    assert not (data_root / ".trash" / str(first.id) / str(first_entry_id)).exists()
    assert not (data_root / ".trash" / str(second.id) / str(second_entry_id)).exists()
