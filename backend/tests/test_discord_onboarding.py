from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_external_api_v1 import _api_client, _headers

from app.api.external.rate_limit import ExternalApiRateLimiter
from app.auth.security import verify_password
from app.main import app
from app.models import DatabaseOption, DiscordAccountLink, ExternalApiAudit, User
from app.options import PostgresOptionsRegistry
from app.users.provisioning import PreparedPassword, UserProvisioningService

DISCORD_ID = "123456789012345678"
ROOT = "/api/external/v1"


@pytest.fixture(autouse=True)
def reset_limiter() -> None:
    app.state.external_api_rate_limiter = ExternalApiRateLimiter()


async def create(
    client: AsyncClient, raw: str, *, key: str = "discord-create-001"
) -> dict[str, Any]:
    result = await client.post(
        f"{ROOT}/users",
        json={"discord_user_id": DISCORD_ID},
        headers={**_headers(raw), "Idempotency-Key": key},
    )
    assert result.status_code == 201, result.text
    return cast(dict[str, Any], result.json())


@pytest.mark.asyncio
async def test_discord_creation_is_unique_across_commands_and_clients(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, raw = await _api_client(db_session, scopes=["users:create", "users:read"])
    first = await create(client, raw)
    replay = await client.post(
        f"{ROOT}/users",
        json={"discord_user_id": DISCORD_ID},
        headers={**_headers(raw), "Idempotency-Key": "discord-create-001"},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == first["id"]
    assert replay.json()["temporary_password"] is None
    _, other = await _api_client(db_session, scopes=["users:create"], raw=raw + "other")
    for credential in (raw, other):
        duplicate = await client.post(
            f"{ROOT}/users",
            json={"discord_user_id": DISCORD_ID},
            headers={**_headers(credential), "Idempotency-Key": "another-command-123"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "discord_account_exists"
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1
    link = await db_session.get(DiscordAccountLink, DISCORD_ID)
    assert link is not None and str(link.user_id) == first["id"]


@pytest.mark.asyncio
async def test_capacity_is_advisory_and_creation_keeps_final_quota_check(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await PostgresOptionsRegistry().initialize(db_session)
    option = await db_session.get(DatabaseOption, "WOS_MAX_USER_ACCOUNTS")
    assert option is not None
    option.integer_value = 1
    await db_session.commit()
    _, raw = await _api_client(db_session, scopes=["users:read", "users:create"])
    capacity = await client.get(f"{ROOT}/capacity", headers=_headers(raw))
    assert capacity.json() == {"used": 0, "maximum": 1, "available": 1, "reached": False}
    assert capacity.headers["cache-control"] == "no-store"
    await create(client, raw)
    full = await client.get(f"{ROOT}/capacity", headers=_headers(raw))
    assert full.json() == {"used": 1, "maximum": 1, "available": 0, "reached": True}
    refused = await client.post(
        f"{ROOT}/users",
        json={"discord_user_id": "223456789012345678"},
        headers={**_headers(raw), "Idempotency-Key": "second-discord-command"},
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "account_quota_reached"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["active", "suspended", "deleted"])
async def test_status_reports_lifecycle_without_exposing_credentials(
    client: AsyncClient,
    db_session: AsyncSession,
    state: str,
) -> None:
    _, raw = await _api_client(db_session, scopes=["users:create", "users:read"])
    created = await create(client, raw)
    user = await db_session.get(User, UUID(created["id"]))
    assert user is not None
    user.is_active = state == "active"
    if state == "deleted":
        user.deleted_at = datetime.now(UTC)
    await db_session.commit()
    status = await client.get(f"{ROOT}/users/discord/{DISCORD_ID}", headers=_headers(raw))
    assert status.status_code == 200
    assert status.json() == {
        "id": created["id"],
        "username": created["username"],
        "is_active": state == "active",
        "deleted": state == "deleted",
        "must_change_credentials": True,
    }
    assert status.headers["cache-control"] == "no-store"
    unknown = await client.get(f"{ROOT}/users/discord/223456789012345678", headers=_headers(raw))
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_recovery_replaces_lost_password_and_stops_after_first_login(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, raw = await _api_client(db_session, scopes=["users:create", "users:recover"])
    created = await create(client, raw)
    endpoint = f"{ROOT}/users/discord/{DISCORD_ID}/initial-credentials"
    recovered = await client.post(endpoint, headers=_headers(raw))
    assert recovered.status_code == 200, recovered.text
    assert recovered.headers["cache-control"] == "no-store"
    credentials = recovered.json()
    assert credentials["id"] == created["id"]
    assert credentials["auth_seed"] == created["auth_seed"]
    assert credentials["temporary_password"] != created["temporary_password"]
    user = await db_session.get(User, UUID(created["id"]))
    assert user is not None
    assert not verify_password(created["temporary_password"], user.password_hash)
    assert verify_password(credentials["temporary_password"], user.password_hash)
    # A second lost delivery can be recovered again; only its latest password remains valid.
    second = await client.post(endpoint, headers=_headers(raw))
    assert second.status_code == 200
    login = await client.post(
        "/api/v1/auth/login",
        json={
            "username": created["username"],
            "password": second.json()["temporary_password"],
        },
    )
    assert login.status_code == 200
    denied = await client.post(endpoint, headers=_headers(raw))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "initial_credentials_unavailable"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ExternalApiAudit)
            .where(
                ExternalApiAudit.action == "users.recover_initial",
            )
        )
        == 2
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["suspended", "deleted", "claimed", "admin"])
async def test_recovery_does_not_change_protected_accounts(
    client: AsyncClient,
    db_session: AsyncSession,
    state: str,
) -> None:
    _, raw = await _api_client(db_session, scopes=["users:create", "users:recover"])
    created = await create(client, raw)
    user = await db_session.get(User, UUID(created["id"]))
    assert user is not None
    original_hash = user.password_hash
    user.is_active = state != "suspended"
    user.is_admin = state == "admin"
    if state == "deleted":
        user.deleted_at = datetime.now(UTC)
    if state == "claimed":
        user.must_change_credentials = False
    await db_session.commit()
    denied = await client.post(
        f"{ROOT}/users/discord/{DISCORD_ID}/initial-credentials",
        headers=_headers(raw),
    )
    assert denied.status_code == 409
    await db_session.refresh(user)
    assert user.password_hash == original_hash


@pytest.mark.asyncio
async def test_new_routes_require_scopes_and_recovery_is_bound_to_creator(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    _, raw = await _api_client(db_session, scopes=["users:create"])
    created = await create(client, raw)
    for method, path in (
        ("GET", "/capacity"),
        ("GET", f"/users/discord/{DISCORD_ID}"),
        ("POST", f"/users/discord/{DISCORD_ID}/initial-credentials"),
    ):
        assert (await client.request(method, ROOT + path)).status_code == 401
        assert (await client.request(method, ROOT + path, headers=_headers(raw))).status_code == 403
    _, other = await _api_client(db_session, scopes=["users:recover"], raw=raw + "other")
    denied = await client.post(
        f"{ROOT}/users/discord/{DISCORD_ID}/initial-credentials",
        headers=_headers(other),
    )
    assert denied.status_code == 404
    user = await db_session.get(User, UUID(created["id"]))
    assert user is not None and verify_password(created["temporary_password"], user.password_hash)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "discord_id", ["name", "012345678901234567", "123", "1" * 21, 123456789012345678]
)
async def test_discord_id_must_be_a_snowflake_string(
    client: AsyncClient,
    db_session: AsyncSession,
    discord_id: str | int,
) -> None:
    _, raw = await _api_client(db_session, scopes=["users:create"])
    result = await client.post(
        f"{ROOT}/users",
        json={"discord_user_id": discord_id},
        headers={**_headers(raw), "Idempotency-Key": "invalid-discord-command"},
    )
    assert result.status_code == 422
    assert await db_session.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.asyncio
async def test_recovery_rechecks_token_revocation_after_password_work(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_client, raw = await _api_client(db_session, scopes=["users:create", "users:recover"])
    created = await create(client, raw)
    original = UserProvisioningService.prepare_password

    async def prepare(
        self: UserProvisioningService,
        password: str | None = None,
    ) -> PreparedPassword:
        prepared = await original(self, password)
        api_client.revoked_at = datetime.now(UTC)
        await db_session.commit()
        return prepared

    monkeypatch.setattr(UserProvisioningService, "prepare_password", prepare)
    denied = await client.post(
        f"{ROOT}/users/discord/{DISCORD_ID}/initial-credentials",
        headers=_headers(raw),
    )
    assert denied.status_code == 401
    user = await db_session.get(User, UUID(created["id"]))
    assert user is not None and verify_password(created["temporary_password"], user.password_hash)
