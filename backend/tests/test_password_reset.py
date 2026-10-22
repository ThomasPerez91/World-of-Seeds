from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_discord_onboarding import DISCORD_ID, create
from test_external_api_v1 import _api_client, _headers

from app.api.external.rate_limit import ExternalApiRateLimiter
from app.auth.rate_limit import LoginIpRateLimiter
from app.auth.security import hash_password, hash_token, verify_password
from app.main import app
from app.models import ExternalApiAudit, ExternalApiClient, PasswordReset, User

ROOT = "/api/external/v1"
CONFIRM = "/api/v1/auth/password-reset/confirm"


@pytest.fixture(autouse=True)
def limiters() -> None:
    app.state.external_api_rate_limiter = ExternalApiRateLimiter()
    app.state.login_ip_rate_limiter = LoginIpRateLimiter()


async def setup(client: AsyncClient, db: AsyncSession) -> tuple[str, dict[str, Any]]:
    _, raw = await _api_client(db, scopes=["users:create", "users:read", "users:reset-password"])
    return raw, await create(client, raw)


async def issue(client: AsyncClient, raw: str) -> str:
    result = await client.post(
        f"{ROOT}/users/discord/{DISCORD_ID}/password-reset", headers=_headers(raw)
    )
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["expires_in"] == 900
    assert set(result.json()) == {"token", "expires_in"}
    return str(result.json()["token"])


@pytest.mark.asyncio
async def test_reset_after_first_login_and_password_change_revokes_sessions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, account = await setup(client, db_session)
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": account["username"], "password": account["temporary_password"]},
        )
    ).status_code == 200
    csrf = client.cookies.get("wos_csrf")
    changed = await client.patch(
        "/api/v1/auth/credentials",
        json={
            "current_password": account["temporary_password"],
            "username": "chosen-name",
            "new_password": "chosen-password-123",
        },
        headers={"X-CSRF-Token": str(csrf)},
    )
    assert changed.status_code == 200
    token = await issue(client, raw)
    user = await db_session.get(User, UUID(account["id"]), populate_existing=True)
    assert user is not None and verify_password("chosen-password-123", user.password_hash)
    grant = await db_session.get(PasswordReset, user.id)
    assert (
        grant is not None
        and grant.token_hash == hash_token(token)
        and token not in repr(grant.__dict__)
    )
    response = await client.post(CONFIRM, json={"token": token, "new_password": "new-password-456"})
    assert response.status_code == 204, response.text
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await client.post(CONFIRM, json={"token": token, "new_password": "another-password-456"})
    ).status_code == 400
    assert (
        await client.post(
            "/api/v1/auth/login",
            json={"username": "chosen-name", "password": "chosen-password-123"},
        )
    ).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/login", json={"username": "chosen-name", "password": "new-password-456"}
        )
    ).status_code == 200
    await db_session.refresh(user)
    assert user.auth_seed == account["auth_seed"] and not user.must_change_credentials
    audits = (await db_session.scalars(select(ExternalApiAudit))).all()
    assert {"password_reset.issue", "password_reset.complete"} <= {a.action for a in audits}
    assert token not in repr([a.__dict__ for a in audits])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "expired",
        "consumed",
        "password_changed",
        "disabled",
        "deleted",
        "admin",
        "client_revoked",
        "scope_removed",
        "key_changed",
    ],
)
async def test_invalidated_grants_cannot_change_password(
    client: AsyncClient, db_session: AsyncSession, reason: str
) -> None:
    raw, account = await setup(client, db_session)
    token = await issue(client, raw)
    user = await db_session.get(User, UUID(account["id"]), populate_existing=True)
    grant = await db_session.get(PasswordReset, UUID(account["id"]))
    assert user is not None and grant is not None
    issuer = await db_session.get(ExternalApiClient, grant.client_id)
    assert issuer is not None
    if reason == "expired":
        grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif reason == "consumed":
        grant.consumed_at = datetime.now(UTC)
    elif reason == "password_changed":
        user.password_hash = hash_password("already-changed-password")
    elif reason == "disabled":
        user.is_active = False
    elif reason == "deleted":
        user.deleted_at = datetime.now(UTC)
    elif reason == "admin":
        user.is_admin = True
    elif reason == "client_revoked":
        issuer.is_active = False
    elif reason == "scope_removed":
        issuer.scopes = ["users:read"]
    elif reason == "key_changed":
        issuer.key_hash = hash_token("new-key")
    previous = user.password_hash
    await db_session.commit()
    result = await client.post(
        CONFIRM, json={"token": token, "new_password": "replacement-password"}
    )
    assert result.status_code == 400
    assert result.json()["detail"]["code"] == "password_reset_unavailable"
    await db_session.refresh(user)
    assert user.password_hash == previous


@pytest.mark.asyncio
async def test_cooldown_rotation_and_creator_independence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, account = await setup(client, db_session)
    old = await issue(client, raw)
    assert (
        await client.post(
            f"{ROOT}/users/discord/{DISCORD_ID}/password-reset", headers=_headers(raw)
        )
    ).status_code == 429
    grant = await db_session.get(PasswordReset, UUID(account["id"]))
    assert grant is not None
    grant.created_at = datetime.now(UTC) - timedelta(minutes=2)
    await db_session.commit()
    _, new_key = await _api_client(db_session, scopes=["users:reset-password"], raw=raw + "new")
    new = await issue(client, new_key)
    assert new != old
    assert (
        await client.post(CONFIRM, json={"token": old, "new_password": "replacement-password"})
    ).status_code == 400
    assert (
        await client.post(CONFIRM, json={"token": new, "new_password": "replacement-password"})
    ).status_code == 204
    user = await db_session.get(User, UUID(account["id"]), populate_existing=True)
    assert user is not None and user.must_change_credentials


@pytest.mark.asyncio
async def test_scope_schema_and_public_budget(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, _ = await setup(client, db_session)
    _, old_key = await _api_client(db_session, scopes=["users:recover"], raw=raw + "old")
    assert (
        await client.post(
            f"{ROOT}/users/discord/{DISCORD_ID}/password-reset", headers=_headers(old_key)
        )
    ).status_code == 403
    malformed = await client.post(
        CONFIRM, json={"token": "SECRET_INVALID_TOKEN", "new_password": "SECRET_SHORT"}
    )
    assert malformed.status_code == 422 and "SECRET" not in malformed.text
    app.state.login_ip_rate_limiter = LoginIpRateLimiter(maximum=1)
    assert (
        await client.post(CONFIRM, json={"token": "Z" * 43, "new_password": "replacement-password"})
    ).status_code == 400
    assert (
        await client.post(CONFIRM, json={"token": "Z" * 43, "new_password": "replacement-password"})
    ).status_code == 429


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["disabled", "deleted", "admin", "missing"])
async def test_issuance_refuses_ineligible_identities(
    client: AsyncClient, db_session: AsyncSession, reason: str
) -> None:
    raw, account = await setup(client, db_session)
    user = await db_session.get(User, UUID(account["id"]))
    assert user is not None
    if reason == "disabled":
        user.is_active = False
    elif reason == "deleted":
        user.deleted_at = datetime.now(UTC)
    elif reason == "admin":
        user.is_admin = True
    await db_session.commit()
    identity = "223456789012345678" if reason == "missing" else DISCORD_ID
    response = await client.post(
        f"{ROOT}/users/discord/{identity}/password-reset", headers=_headers(raw)
    )
    assert (
        response.status_code == 409
        and response.json()["error"]["code"] == "password_reset_unavailable"
    )
    assert await db_session.scalar(select(PasswordReset)) is None


@pytest.mark.asyncio
async def test_suspension_invalidates_link_even_after_reactivation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.auth.service import set_managed_user_active

    raw, account = await setup(client, db_session)
    token = await issue(client, raw)
    identity = UUID(account["id"])
    await set_managed_user_active(db_session, user_id=identity, is_active=False)
    await set_managed_user_active(db_session, user_id=identity, is_active=True)
    result = await client.post(
        CONFIRM, json={"token": token, "new_password": "replacement-password"}
    )
    assert result.status_code == 400
