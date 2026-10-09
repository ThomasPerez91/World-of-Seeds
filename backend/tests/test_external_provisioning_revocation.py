import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_token
from app.models import (
    ExternalApiAudit,
    ExternalApiClient,
    ExternalApiIdempotency,
    User,
    UserProvisioningAudit,
)
from app.users.provisioning import PreparedPassword, UserProvisioningService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "expected_status", "expected_code"),
    [
        ("disable", 401, "api_client_disabled"),
        ("revoke", 401, "api_client_disabled"),
        ("scope", 403, "insufficient_scope"),
        ("rotate", 401, "invalid_api_key"),
    ],
)
async def test_client_authorization_changed_during_password_work_cannot_create_account(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
    expected_status: int,
    expected_code: str,
) -> None:
    raw = "wos_live_FAKE_REVOCATION_TEST_KEY_abcdefghijklmnopqrstuvwxyz"
    api_client = ExternalApiClient(
        name="Revocation test",
        key_hash=hash_token(raw),
        key_prefix="wos_live_FAKE_TEST",
        scopes=["users:create"],
    )
    db_session.add(api_client)
    await db_session.commit()
    client_id = api_client.id
    original = UserProvisioningService.prepare_password

    async def prepare(
        self: UserProvisioningService, password: str | None = None
    ) -> PreparedPassword:
        prepared = await original(self, password)
        statement = update(ExternalApiClient).where(ExternalApiClient.id == client_id)
        if change == "disable":
            statement = statement.values(is_active=False)
        elif change == "revoke":
            statement = statement.values(revoked_at=datetime.now(UTC))
        elif change == "scope":
            statement = statement.values(scopes=["downloads:read"])
        else:
            statement = statement.values(key_hash=hash_token("wos_live_FAKE_NEW_KEY"))
        await db_session.execute(statement.execution_options(synchronize_session=False))
        await db_session.commit()
        return prepared

    monkeypatch.setattr(UserProvisioningService, "prepare_password", prepare)
    response = await client.post(
        "/api/external/v1/users",
        json={"username": "revocation-race-user"},
        headers={"Authorization": f"Bearer {raw}", "Idempotency-Key": "revocation-test-001"},
    )
    assert response.status_code == expected_status, response.text
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert "auth_seed" not in response.text and "temporary_password" not in response.text
    for model in (User, UserProvisioningAudit, ExternalApiAudit, ExternalApiIdempotency):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("WOS_DATABASE_URL", "").startswith("postgresql"),
    reason="PostgreSQL independent-transaction revocation race",
)
@pytest.mark.parametrize("order", ["revocation_first", "creation_first"])
async def test_postgres_creation_and_revocation_have_a_transactional_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, order: str
) -> None:
    from collections.abc import AsyncIterator
    from uuid import UUID, uuid4

    from httpx import ASGITransport
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.external import users as external_users
    from app.api.external.dependencies import lock_external_client_authorization
    from app.core.config import Settings
    from app.core.database import get_db_session
    from app.main import create_app

    engine = create_async_engine(os.environ["WOS_DATABASE_URL"], pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    application = create_app(Settings(data_root=tmp_path))

    async def database() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    application.dependency_overrides[get_db_session] = database
    suffix = uuid4().hex
    username = f"revocation-{suffix[:16]}"
    raw = f"wos_live_FAKE_PG_REVOCATION_{suffix}"
    api_client = ExternalApiClient(
        name="PostgreSQL revocation race",
        key_hash=hash_token(raw),
        key_prefix="wos_live_FAKE_TEST",
        scopes=["users:create"],
    )
    async with sessions() as setup:
        setup.add(api_client)
        await setup.commit()
    client_id = api_client.id
    entered, release, revoking = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_prepare = UserProvisioningService.prepare_password
    original_validate = lock_external_client_authorization

    async def prepare(
        self: UserProvisioningService, password: str | None = None
    ) -> PreparedPassword:
        prepared = await original_prepare(self, password)
        entered.set()
        await release.wait()
        return prepared

    async def validate(db: AsyncSession, *, client_id: UUID, key_hash: str, scope: str) -> None:
        await original_validate(db, client_id=client_id, key_hash=key_hash, scope=scope)
        entered.set()
        await release.wait()

    if order == "revocation_first":
        monkeypatch.setattr(UserProvisioningService, "prepare_password", prepare)
    else:
        monkeypatch.setattr(external_users, "lock_external_client_authorization", validate)

    async def revoke() -> None:
        async with sessions() as admin:
            revoking.set()
            await admin.execute(
                update(ExternalApiClient)
                .where(ExternalApiClient.id == client_id)
                .values(is_active=False, revoked_at=datetime.now(UTC))
            )
            await admin.commit()

    headers = {"Authorization": f"Bearer {raw}", "Idempotency-Key": "pg-revocation-test-001"}
    request_task: asyncio.Task[Response] | None = None
    revocation: asyncio.Task[None] | None = None
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as http:
            request_task = asyncio.create_task(
                http.post(
                    "/api/external/v1/users",
                    json={"username": username},
                    headers=headers,
                )
            )
            await asyncio.wait_for(entered.wait(), 5)
            revocation = asyncio.create_task(revoke())
            await asyncio.wait_for(revoking.wait(), 5)
            if order == "revocation_first":
                # The password work has retained neither a SQL connection nor an authorization lock.
                await asyncio.wait_for(revocation, 5)
            else:
                # The administrator cannot commit across a creation's authorization row lock.
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(revocation), 0.2)
            release.set()
            response = await asyncio.wait_for(request_task, 5)
            await asyncio.wait_for(revocation, 5)
            assert response.status_code == (401 if order == "revocation_first" else 201)
            denied = await http.post(
                "/api/external/v1/users",
                json={"username": username},
                headers={**headers, "Idempotency-Key": "pg-revocation-test-002"},
            )
            assert denied.status_code == 401
        async with sessions() as check:
            count = int(
                await check.scalar(
                    select(func.count()).select_from(User).where(User.username == username)
                )
                or 0
            )
            assert count == (0 if order == "revocation_first" else 1)
            for model in (ExternalApiAudit, ExternalApiIdempotency):
                assert (
                    await check.scalar(
                        select(func.count()).select_from(model).where(model.client_id == client_id)
                    )
                    == count
                )
    finally:
        release.set()
        # Complete/cancel in-flight transactions before deleting this test's isolated rows.
        pending_tasks = [task for task in (request_task, revocation) if task is not None]
        if pending_tasks:
            await asyncio.wait_for(asyncio.gather(*pending_tasks, return_exceptions=True), 10)
        async with sessions() as cleanup:
            await cleanup.execute(
                delete(ExternalApiIdempotency).where(ExternalApiIdempotency.client_id == client_id)
            )
            await cleanup.execute(
                delete(ExternalApiAudit).where(ExternalApiAudit.client_id == client_id)
            )
            await cleanup.execute(
                delete(UserProvisioningAudit).where(
                    UserProvisioningAudit.external_client_id == client_id
                )
            )
            await cleanup.execute(delete(User).where(User.username == username))
            await cleanup.execute(
                delete(ExternalApiClient).where(ExternalApiClient.id == client_id)
            )
            await cleanup.commit()
        await application.state.redis_coordinator.aclose()
        await engine.dispose()
