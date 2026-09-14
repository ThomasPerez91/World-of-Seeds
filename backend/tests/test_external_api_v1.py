import asyncio
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.external.downloads import _state
from app.api.external.rate_limit import ExternalApiRateLimiter
from app.auth.security import AUTH_SEED_ALPHABET, generate_auth_seed, hash_password, hash_token
from app.main import app
from app.models import (
    DatabaseOption,
    ExternalApiAudit,
    ExternalApiClient,
    ExternalApiIdempotency,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentRequest,
    TorrentRequestState,
    User,
    UserProvisioningAudit,
)
from app.options import PostgresOptionsRegistry
from app.users import UserAccountQuotaReachedError, UserProvisioningService


async def _user(
    db: AsyncSession,
    username: str,
    *,
    active: bool = True,
    deleted: bool = False,
    admin: bool = False,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password("correct-horse-battery"),
        is_active=active,
        is_admin=admin,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _api_client(
    db: AsyncSession,
    *,
    scopes: list[str],
    active: bool = True,
    raw: str = "wos_live_FAKE_TEST_KEY_abcdefghijklmnopqrstuvwxyz",
) -> tuple[ExternalApiClient, str]:
    client = ExternalApiClient(
        name="Test integration",
        key_hash=hash_token(raw),
        key_prefix="wos_live_FAKE_TEST…",
        scopes=scopes,
        is_active=active,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client, raw


def _headers(raw: str, seed: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {raw}"}
    if seed is not None:
        headers["X-WOS-User-Seed"] = seed
    return headers


async def _login_admin(client: AsyncClient, db: AsyncSession) -> dict[str, str]:
    await _user(db, "external-admin", admin=True)
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "external-admin", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    csrf = client.cookies.get("wos_csrf")
    assert csrf is not None
    return {"X-CSRF-Token": csrf}


def test_auth_seed_generator_is_exact_base62_and_distinct() -> None:
    seeds = {generate_auth_seed() for _ in range(100)}
    assert len(seeds) == 100
    assert all(len(seed) == 25 for seed in seeds)
    assert all(set(seed) <= set(AUTH_SEED_ALPHABET) for seed in seeds)


@pytest.mark.parametrize(
    ("request_state", "torrent_state", "desired_active", "qb_state", "cooldown", "expected"),
    [
        (TorrentRequestState.REQUESTED, ManagedTorrentState.PENDING, False, None, False, "waiting"),
        (
            TorrentRequestState.ACTIVE,
            ManagedTorrentState.DOWNLOADING,
            True,
            None,
            False,
            "downloading",
        ),
        (
            TorrentRequestState.ACTIVE,
            ManagedTorrentState.DOWNLOADING,
            True,
            "stalledDL",
            False,
            "stalled",
        ),
        (TorrentRequestState.ACTIVE, ManagedTorrentState.RETRY_WAIT, False, None, True, "cooldown"),
        (TorrentRequestState.READY, ManagedTorrentState.READY, False, None, False, "ready"),
        (TorrentRequestState.ACTIVE, ManagedTorrentState.ERROR, False, None, False, "error"),
    ],
)
def test_external_download_states_are_stable(
    request_state: TorrentRequestState,
    torrent_state: ManagedTorrentState,
    desired_active: bool,
    qb_state: str | None,
    cooldown: bool,
    expected: str,
) -> None:
    now = datetime.now(UTC)
    request = TorrentRequest(user_id=uuid4(), managed_torrent_id=uuid4(), state=request_state)
    torrent = ManagedTorrent(
        info_hash="f" * 40,
        name="State fixture",
        total_size=1,
        state=torrent_state,
        desired_active=desired_active,
        qb_state=qb_state,
        scheduler_retry_at=now + timedelta(minutes=1) if cooldown else None,
    )
    assert _state(request, torrent, now) == expected


@pytest.mark.asyncio
async def test_admin_client_management_shows_raw_key_once_and_user_seed_is_specialized(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    headers = await _login_admin(client, db_session)
    created = await client.post(
        "/api/v1/admin/external-api-clients",
        json={"name": "Discord bot", "scopes": ["users:create", "downloads:read"]},
        headers=headers,
    )
    assert created.status_code == 201
    raw_key = created.json()["api_key"]
    assert raw_key.startswith("wos_live_")
    listing = await client.get("/api/v1/admin/external-api-clients")
    assert listing.status_code == 200
    assert "api_key" not in listing.text
    assert raw_key not in listing.text
    users = await client.get("/api/v1/admin/users")
    assert "auth_seed" not in users.text
    seed = await client.get("/api/v1/auth/auth-seed")
    assert seed.status_code == 200
    assert len(seed.json()["auth_seed"]) == 25
    assert seed.headers["cache-control"] == "no-store"

    revoked = await client.delete(
        f"/api/v1/admin/external-api-clients/{created.json()['id']}", headers=headers
    )
    assert revoked.status_code == 204
    denied = await client.get(
        "/api/external/v1/me/downloads", headers=_headers(raw_key, seed.json()["auth_seed"])
    )
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "api_client_disabled"


@pytest.mark.asyncio
async def test_admin_creation_returns_stable_quota_error(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    headers = await _login_admin(client, db_session)
    await PostgresOptionsRegistry().initialize(db_session)
    option = await db_session.get(DatabaseOption, "WOS_MAX_USER_ACCOUNTS")
    assert option is not None
    option.integer_value = 1
    await db_session.commit()
    quota = await client.get("/api/v1/admin/users/quota")
    assert quota.json() == {"used": 1, "maximum": 1, "reached": True}
    rejected = await client.post("/api/v1/admin/users", headers=headers)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "account_quota_reached"


@pytest.mark.asyncio
async def test_quota_counts_admin_disabled_and_ignores_soft_deleted(
    db_session: AsyncSession,
) -> None:
    await PostgresOptionsRegistry().initialize(db_session)
    option = await db_session.get(DatabaseOption, "WOS_MAX_USER_ACCOUNTS")
    assert option is not None
    option.integer_value = 3
    await _user(db_session, "quota-admin", admin=True)
    await _user(db_session, "quota-disabled", active=False)
    await _user(db_session, "quota-deleted", deleted=True)

    result = await UserProvisioningService().provision(db_session, source="admin")
    await db_session.commit()
    assert len(result.user.auth_seed) == 25
    with pytest.raises(UserAccountQuotaReachedError):
        await UserProvisioningService().provision(db_session, source="admin")
    await db_session.rollback()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("WOS_DATABASE_URL", "").startswith("postgresql"),
    reason="PostgreSQL transaction-lock race test",
)
async def test_postgres_quota_race_allows_exactly_one_creation() -> None:
    engine = create_async_engine(os.environ["WOS_DATABASE_URL"], pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    admin = User(
        username="quota-race-admin",
        password_hash=hash_password("correct-horse-battery"),
        is_admin=True,
    )
    async with sessions() as setup:
        await PostgresOptionsRegistry().initialize(setup)
        setup.add(admin)
        await setup.flush()
        current = int(
            await setup.scalar(
                select(func.count()).select_from(User).where(User.deleted_at.is_(None))
            )
            or 0
        )
        option = await setup.get(DatabaseOption, "WOS_MAX_USER_ACCOUNTS")
        assert option is not None
        option.integer_value = current + 1
        await setup.commit()

    async def attempt() -> UUID | None:
        async with sessions() as session:
            try:
                result = await UserProvisioningService().provision(session, source="admin")
                await session.commit()
                return result.user.id
            except UserAccountQuotaReachedError:
                await session.rollback()
                return None

    results = await asyncio.gather(attempt(), attempt())
    assert results.count(None) == 1
    created_ids = [value for value in results if value is not None]
    assert len(created_ids) == 1

    async with sessions() as cleanup:
        await cleanup.execute(
            delete(UserProvisioningAudit).where(
                UserProvisioningAudit.user_id.in_([admin.id, *created_ids])
            )
        )
        await cleanup.execute(delete(User).where(User.id.in_([admin.id, *created_ids])))
        option = await cleanup.get(DatabaseOption, "WOS_MAX_USER_ACCOUNTS")
        assert option is not None
        option.integer_value = 100
        await cleanup.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_seed_collision_retries_and_password_change_does_not_rotate(
    db_session: AsyncSession,
) -> None:
    existing = await _user(db_session, "seed-existing")
    existing.auth_seed = "A" * 25
    await db_session.commit()
    with patch("app.users.provisioning.generate_auth_seed", side_effect=["A" * 25, "B" * 25]):
        created = await UserProvisioningService().provision(db_session, source="admin")
    await db_session.commit()
    assert created.user.auth_seed == "B" * 25
    stable_seed = created.user.auth_seed
    created.user.password_hash = hash_password("another-correct-password")
    created.user.is_active = False
    await db_session.commit()
    assert created.user.auth_seed == stable_seed


@pytest.mark.asyncio
async def test_external_client_auth_scopes_create_and_idempotency(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    api_client, raw = await _api_client(db_session, scopes=["users:create"])
    headers = {**_headers(raw), "Idempotency-Key": "command-create-001"}
    created = await client.post(
        "/api/external/v1/users", json={"username": "external-user"}, headers=headers
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["username"] == "external-user"
    assert body["temporary_password"]
    assert len(body["auth_seed"]) == 25
    assert body["must_change_credentials"] is True
    user = await db_session.scalar(select(User).where(User.username == "external-user"))
    assert user is not None and user.is_admin is False
    assert (await db_session.scalars(select(UserProvisioningAudit))).all()
    assert (await db_session.scalars(select(ExternalApiAudit))).all()
    assert (await db_session.scalars(select(ExternalApiIdempotency))).all()
    assert raw not in repr(api_client)
    assert api_client.key_hash == hash_token(raw)

    replay = await client.post(
        "/api/external/v1/users", json={"username": "external-user"}, headers=headers
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]
    assert replay.json()["temporary_password"] is None
    assert replay.json()["idempotent_replay"] is True
    assert (
        len((await db_session.scalars(select(User).where(User.username == "external-user"))).all())
        == 1
    )

    missing_scope_client, missing_scope_key = await _api_client(
        db_session,
        scopes=["downloads:read"],
        raw="wos_live_FAKE_SCOPE_KEY_abcdefghijklmnopqrstuvwxyz",
    )
    denied = await client.post(
        "/api/external/v1/users",
        json={},
        headers={**_headers(missing_scope_key), "Idempotency-Key": "command-create-002"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "insufficient_scope"
    assert missing_scope_client.id != api_client.id


@pytest.mark.asyncio
async def test_external_auth_failures_health_request_id_and_rate_limit(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    health = await client.get("/api/external/v1/health")
    assert health.json() == {"status": "ok", "api_version": "1"}
    assert health.headers["x-request-id"]
    invalid = await client.get(
        "/api/external/v1/me/downloads",
        headers=_headers("wos_live_WRONG_FAKE_KEY_abcdefghijklmnopqrstuvwxyz", "A" * 25),
    )
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "invalid_api_key"
    disabled, raw = await _api_client(db_session, scopes=["downloads:read"], active=False)
    denied = await client.get("/api/external/v1/me/downloads", headers=_headers(raw, "A" * 25))
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "api_client_disabled"
    assert disabled.is_active is False

    app.state.external_api_rate_limiter = ExternalApiRateLimiter(maximum=1, window_seconds=60)
    first = await client.get(
        "/api/external/v1/me/downloads", headers=_headers("wos_live_ANOTHER_WRONG_KEY", "A" * 25)
    )
    second = await client.get(
        "/api/external/v1/me/downloads", headers=_headers("wos_live_ANOTHER_WRONG_KEY", "A" * 25)
    )
    assert first.status_code == 401
    assert second.status_code == 429
    assert second.headers["retry-after"]
    app.state.external_api_rate_limiter = ExternalApiRateLimiter()


@pytest.mark.asyncio
async def test_external_downloads_are_isolated_and_normalized(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await _user(db_session, "downloads-owner")
    other = await _user(db_session, "downloads-other")
    _api, raw = await _api_client(db_session, scopes=["downloads:read"])
    now = datetime.now(UTC)
    ready_torrent = ManagedTorrent(
        info_hash="a" * 40,
        name="Owner ready",
        total_size=123,
        state=ManagedTorrentState.READY,
        progress=1,
    )
    waiting_torrent = ManagedTorrent(
        info_hash="b" * 40,
        name="Other waiting",
        total_size=456,
        state=ManagedTorrentState.PENDING,
        progress=0,
    )
    db_session.add_all([ready_torrent, waiting_torrent])
    await db_session.flush()
    db_session.add_all(
        [
            TorrentRequest(
                user_id=owner.id,
                managed_torrent_id=ready_torrent.id,
                state=TorrentRequestState.READY,
                ready_at=now,
                unsubscribe_at=now + timedelta(seconds=120),
            ),
            TorrentRequest(
                user_id=other.id,
                managed_torrent_id=waiting_torrent.id,
                state=TorrentRequestState.REQUESTED,
            ),
        ]
    )
    await db_session.commit()
    response = await client.get(
        "/api/external/v1/me/downloads?limit=10", headers=_headers(raw, owner.auth_seed)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert [item["name"] for item in body["items"]] == ["Owner ready"]
    assert body["items"][0]["state"] == "ready"
    assert body["items"][0]["eta_seconds"] is None
    assert 0 <= body["items"][0]["access_remaining_seconds"] <= 120
    assert "storage_key" not in response.text

    invalid_seed = await client.get(
        "/api/external/v1/me/downloads", headers=_headers(raw, "!" * 25)
    )
    assert invalid_seed.status_code == 401
    assert invalid_seed.json()["error"]["code"] == "invalid_user_seed"
