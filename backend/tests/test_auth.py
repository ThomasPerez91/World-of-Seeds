from datetime import UTC, datetime
from pathlib import Path

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
        assert response.json()["detail"] == "Invalid username or password"

    locked = await client.post(
        "/api/v1/auth/login",
        json={"username": "thomas", "password": "correct-horse-battery"},
    )
    assert locked.status_code == 429


@pytest.mark.asyncio
async def test_admin_generates_one_time_credentials_and_user_changes_them(
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
        "/api/v1/admin/users/temporary",
        json={"expires_in_days": 3},
        headers=csrf_header(client),
    )
    assert generated.status_code == 200
    temporary = generated.json()
    assert temporary["user"]["username"].startswith("guest-")
    assert temporary["user"]["must_change_credentials"] is True
    assert len(temporary["temporary_password"]) >= 12
    temporary_workspace = data_root / "users" / temporary["user"]["username"]
    assert {entry.name for entry in temporary_workspace.iterdir()} == {"downloads", "watch"}

    temporary_user = await db_session.scalar(
        select(User).where(User.username == temporary["user"]["username"])
    )
    assert temporary_user is not None
    assert temporary_user.password_hash.startswith("$argon2id$")
    assert verify_password(temporary["temporary_password"], temporary_user.password_hash)
    assert temporary_user.expires_at is not None
    assert temporary_user.expires_at.replace(tzinfo=UTC) > datetime.now(UTC)

    client.cookies.clear()
    await login(client, temporary_user.username, temporary["temporary_password"])
    forbidden = await client.get("/api/v1/admin/users")
    assert forbidden.status_code == 403

    changed = await client.patch(
        "/api/v1/auth/credentials",
        json={
            "current_password": temporary["temporary_password"],
            "username": "invitee",
            "new_password": "invitee-password-long",
        },
        headers=csrf_header(client),
    )
    assert changed.status_code == 200
    assert changed.json()["user"]["username"] == "invitee"
    assert changed.json()["user"]["must_change_credentials"] is False
    assert not temporary_workspace.exists()
    assert (data_root / "users" / "invitee" / "downloads").is_dir()
    assert (data_root / "users" / "invitee" / "watch").is_dir()

    active_sessions = (
        await db_session.scalars(
            select(UserSession).where(
                UserSession.user_id == temporary_user.id,
                UserSession.revoked_at.is_(None),
            )
        )
    ).all()
    assert len(active_sessions) == 1

    client.cookies.clear()
    await login(client, admin.username, "admin-password-long")
    users = await client.get("/api/v1/admin/users")
    assert users.status_code == 200
    assert {user["username"] for user in users.json()} == {"admin", "invitee"}


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
