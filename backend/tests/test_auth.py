from pathlib import Path
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password, verify_password
from app.models import User, UserSession


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    is_admin: bool = False,
    must_change_credentials: bool = False,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=is_admin,
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


def csrf_header(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get("wos_csrf")
    assert token is not None
    return {"X-CSRF-Token": token}


@pytest.mark.asyncio
async def test_session_cookie_csrf_and_logout(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await create_user(db_session, username="thomas", password="correct-horse-battery")

    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "thomas", "password": "correct-horse-battery"},
    )

    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(cookie for cookie in cookies if cookie.startswith("wos_session="))
    csrf_cookie = next(cookie for cookie in cookies if cookie.startswith("wos_csrf="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=strict" in session_cookie
    assert "HttpOnly" not in csrf_cookie
    assert (await client.get("/api/v1/auth/me")).status_code == 200

    valid_csrf = csrf_header(client)
    tampered = await client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": "tampered-token"},
    )
    assert tampered.status_code == 403

    missing_csrf = await client.post("/api/v1/auth/logout")
    assert missing_csrf.status_code == 403

    logged_out = await client.post("/api/v1/auth/logout", headers=valid_csrf)
    assert logged_out.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_failed_logins_are_generic_and_throttled(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await create_user(db_session, username="thomas", password="correct-horse-battery")

    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "thomas", "password": "incorrect-password"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == {
            "code": "authentication_failed",
            "message": "Invalid username or password",
            "field": None,
        }

    locked = await client.post(
        "/api/v1/auth/login",
        json={"username": "thomas", "password": "correct-horse-battery"},
    )
    assert locked.status_code == 429


@pytest.mark.asyncio
async def test_user_persists_supported_interface_locale(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    user = await create_user(
        db_session,
        username="thomas",
        password="correct-horse-battery",
        must_change_credentials=True,
    )
    await login(client, "thomas", "correct-horse-battery")

    changed = await client.patch(
        "/api/v1/auth/locale",
        json={"preferred_locale": "en"},
        headers=csrf_header(client),
    )

    assert changed.status_code == 200
    assert changed.json()["user"]["preferred_locale"] == "en"
    await db_session.refresh(user)
    assert user.preferred_locale == "en"

    rejected = await client.patch(
        "/api/v1/auth/locale",
        json={"preferred_locale": "de"},
        headers=csrf_header(client),
    )
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_admin_generates_initial_credentials_and_user_changes_them(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    admin = await create_user(
        db_session,
        username="admin",
        password="admin-password-long",
        is_admin=True,
    )
    await login(client, "admin", "admin-password-long")

    generated = await client.post(
        "/api/v1/admin/users",
        headers=csrf_header(client),
    )
    assert generated.status_code == 200
    initial = generated.json()
    assert initial["user"]["username"].startswith("guest-")
    assert initial["user"]["must_change_credentials"] is True
    assert "expires_at" not in initial["user"]
    assert len(initial["initial_password"]) >= 12
    initial_workspace = data_root / initial["user"]["username"]
    assert {entry.name for entry in initial_workspace.iterdir()} == {"downloads"}

    generated_user = await db_session.scalar(
        select(User).where(User.username == initial["user"]["username"])
    )
    assert generated_user is not None
    assert generated_user.password_hash.startswith("$argon2id$")
    assert verify_password(initial["initial_password"], generated_user.password_hash)

    client.cookies.clear()
    await login(client, generated_user.username, initial["initial_password"])
    forbidden = await client.get("/api/v1/admin/users")
    assert forbidden.status_code == 403

    separate_update_forbidden = await client.patch(
        "/api/v1/auth/username",
        json={"username": "not-ready"},
        headers=csrf_header(client),
    )
    assert separate_update_forbidden.status_code == 403

    case_insensitive_collision = await client.patch(
        "/api/v1/auth/credentials",
        json={
            "current_password": initial["initial_password"],
            "username": "ADMIN",
            "new_password": "invitee-password-long",
        },
        headers=csrf_header(client),
    )
    assert case_insensitive_collision.status_code == 409
    assert initial_workspace.is_dir()

    changed = await client.patch(
        "/api/v1/auth/credentials",
        json={
            "current_password": initial["initial_password"],
            "username": "Shadowsun",
            "new_password": "invitee-password-long",
        },
        headers=csrf_header(client),
    )
    assert changed.status_code == 200
    assert changed.json()["user"]["username"] == "Shadowsun"
    assert changed.json()["user"]["must_change_credentials"] is False
    assert not initial_workspace.exists()
    assert (data_root / "Shadowsun" / "downloads").is_dir()

    active_sessions = (
        await db_session.scalars(
            select(UserSession).where(
                UserSession.user_id == generated_user.id,
                UserSession.revoked_at.is_(None),
            )
        )
    ).all()
    assert len(active_sessions) == 1

    client.cookies.clear()
    await login(client, "shadowsun", "invitee-password-long")

    client.cookies.clear()
    await login(client, admin.username, "admin-password-long")
    users = await client.get("/api/v1/admin/users")
    assert users.status_code == 200
    assert {user["username"] for user in users.json()} == {"admin", "Shadowsun"}


@pytest.mark.asyncio
async def test_user_updates_username_then_password_in_separate_flows(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    account = await create_user(
        db_session,
        username="thomas",
        password="current-password-long",
    )
    workspace = data_root / "thomas"
    (workspace / "downloads").mkdir(parents=True)
    marker = workspace / "downloads" / "movie.mkv"
    marker.write_bytes(b"content")
    await login(client, "thomas", "current-password-long")

    renamed = await client.patch(
        "/api/v1/auth/username",
        json={"username": "Shadowsun"},
        headers=csrf_header(client),
    )

    assert renamed.status_code == 200
    assert renamed.json()["user"]["username"] == "Shadowsun"
    assert not workspace.exists()
    assert (data_root / "Shadowsun" / "downloads" / "movie.mkv").read_bytes() == b"content"
    assert (await client.get("/api/v1/auth/me")).json()["user"]["username"] == "Shadowsun"

    wrong_password = await client.patch(
        "/api/v1/auth/password",
        json={
            "current_password": "wrong-password",
            "new_password": "new-password-long",
        },
        headers=csrf_header(client),
    )
    assert wrong_password.status_code == 401
    assert (await client.get("/api/v1/auth/me")).status_code == 200

    changed_password = await client.patch(
        "/api/v1/auth/password",
        json={
            "current_password": "current-password-long",
            "new_password": "new-password-long",
        },
        headers=csrf_header(client),
    )
    assert changed_password.status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 401

    active_sessions = (
        await db_session.scalars(
            select(UserSession).where(
                UserSession.user_id == account.id,
                UserSession.revoked_at.is_(None),
            )
        )
    ).all()
    assert active_sessions == []

    client.cookies.clear()
    rejected_old_password = await client.post(
        "/api/v1/auth/login",
        json={"username": "Shadowsun", "password": "current-password-long"},
    )
    assert rejected_old_password.status_code == 401
    await login(client, "shadowsun", "new-password-long")


@pytest.mark.asyncio
async def test_admin_can_suspend_resume_and_delete_access_without_removing_files(
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
    await login(client, "admin", "admin-password-long")
    generated = await client.post("/api/v1/admin/users", headers=csrf_header(client))
    assert generated.status_code == 200
    credentials = generated.json()
    user_id = UUID(credentials["user"]["id"])
    username = credentials["user"]["username"]
    password = credentials["initial_password"]
    marker = data_root / username / "downloads" / "keep-me.mkv"
    marker.write_bytes(b"content")

    client.cookies.clear()
    await login(client, username, password)
    client.cookies.clear()
    await login(client, "admin", "admin-password-long")

    suspended = await client.patch(
        f"/api/v1/admin/users/{user_id}/status",
        json={"is_active": False},
        headers=csrf_header(client),
    )
    assert suspended.status_code == 200
    assert suspended.json()["is_active"] is False
    active_session_count = len(
        (
            await db_session.scalars(
                select(UserSession).where(
                    UserSession.user_id == user_id,
                    UserSession.revoked_at.is_(None),
                )
            )
        ).all()
    )
    assert active_session_count == 0

    client.cookies.clear()
    rejected = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert rejected.status_code == 401

    client.cookies.clear()
    await login(client, "admin", "admin-password-long")
    resumed = await client.patch(
        f"/api/v1/admin/users/{user_id}/status",
        json={"is_active": True},
        headers=csrf_header(client),
    )
    assert resumed.status_code == 200
    assert resumed.json()["is_active"] is True

    deleted = await client.delete(
        f"/api/v1/admin/users/{user_id}",
        headers=csrf_header(client),
    )
    assert deleted.status_code == 204
    assert marker.read_bytes() == b"content"

    deleted_user = await db_session.get(User, user_id)
    assert deleted_user is not None
    assert deleted_user.is_active is False
    assert deleted_user.deleted_at is not None
    users = await client.get("/api/v1/admin/users")
    assert users.status_code == 200
    assert username not in {user["username"] for user in users.json()}


@pytest.mark.asyncio
async def test_admin_account_is_protected_from_status_and_delete_operations(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    admin = await create_user(
        db_session,
        username="admin",
        password="admin-password-long",
        is_admin=True,
    )
    await login(client, "admin", "admin-password-long")

    suspended = await client.patch(
        f"/api/v1/admin/users/{admin.id}/status",
        json={"is_active": False},
        headers=csrf_header(client),
    )
    assert suspended.status_code == 403

    deleted = await client.delete(
        f"/api/v1/admin/users/{admin.id}",
        headers=csrf_header(client),
    )
    assert deleted.status_code == 403

    await db_session.refresh(admin)
    assert admin.is_active is True
    assert admin.deleted_at is None


@pytest.mark.asyncio
async def test_security_headers_are_applied(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"


@pytest.mark.asyncio
async def test_security_headers_are_applied_to_rejected_hosts(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/health/live",
        headers={"Host": "unexpected.example"},
    )

    assert response.status_code == 400
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
