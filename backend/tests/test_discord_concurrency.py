import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_storage_admission_concurrency import postgres_storage as postgres_storage

from app.api.external.errors import ExternalApiError
from app.api.external.schemas import ExternalUserCreateRequest
from app.api.external.users import create_external_user, recover_initial_credentials
from app.auth.security import hash_token
from app.models import (
    DiscordAccountLink,
    ExternalApiAudit,
    ExternalApiClient,
    ExternalApiIdempotency,
    User,
    UserProvisioningAudit,
)


def request() -> Request:
    return Request({"type": "http", "state": {"external_request_id": uuid4().hex}})


async def cleanup(sessions: async_sessionmaker[AsyncSession], client_ids: list[UUID]) -> None:
    async with sessions() as db, db.begin():
        await db.execute(
            delete(DiscordAccountLink).where(DiscordAccountLink.client_id.in_(client_ids))
        )
        await db.execute(
            delete(ExternalApiIdempotency).where(ExternalApiIdempotency.client_id.in_(client_ids))
        )
        await db.execute(delete(ExternalApiAudit).where(ExternalApiAudit.client_id.in_(client_ids)))
        await db.execute(
            delete(UserProvisioningAudit).where(
                UserProvisioningAudit.external_client_id.in_(client_ids)
            )
        )
        await db.execute(delete(ExternalApiClient).where(ExternalApiClient.id.in_(client_ids)))


@pytest.mark.asyncio
async def test_two_clients_cannot_create_two_accounts_for_one_discord_identity(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
) -> None:
    sessions, prefix = postgres_storage
    discord_id = str(100000000000000000 + uuid4().int % 900000000000000000)
    async with sessions() as db, db.begin():
        clients = [
            ExternalApiClient(
                name=prefix,
                key_hash=hash_token(uuid4().hex),
                key_prefix="test",
                scopes=["users:create"],
            )
            for _ in range(2)
        ]
        db.add_all(clients)
    client_ids = [c.id for c in clients]
    try:

        async def attempt(index: int) -> str:
            async with sessions() as db:
                api_client = await db.get(ExternalApiClient, client_ids[index])
                assert api_client is not None
                await db.commit()
                try:
                    result = await create_external_user(
                        ExternalUserCreateRequest(
                            username=f"{prefix}-{index}", discord_user_id=discord_id
                        ),
                        request(),
                        Response(),
                        db,
                        api_client,
                        f"{prefix}-{index}",
                    )
                    return str(result.id)
                except ExternalApiError as exc:
                    await db.rollback()
                    assert exc.code == "discord_account_exists"
                    return "duplicate"

        results = await asyncio.wait_for(asyncio.gather(attempt(0), attempt(1)), timeout=15)
        assert results.count("duplicate") == 1
        async with sessions() as db:
            users = (await db.scalars(select(User).where(User.username.startswith(prefix)))).all()
            assert len(users) == 1
    finally:
        await cleanup(sessions, client_ids)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["login", "suspend"])
async def test_recovery_rechecks_account_after_waiting_for_its_lock(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    sessions, prefix = postgres_storage
    discord_id = str(100000000000000000 + uuid4().int % 900000000000000000)
    async with sessions() as db, db.begin():
        api_client = ExternalApiClient(
            name=prefix,
            key_hash=hash_token(uuid4().hex),
            key_prefix="test",
            scopes=["users:recover"],
        )
        user = User(username=prefix, password_hash="unchanged", must_change_credentials=True)
        db.add_all([api_client, user])
        await db.flush()
        db.add(
            DiscordAccountLink(discord_user_id=discord_id, user_id=user.id, client_id=api_client.id)
        )
    client_id, user_id = api_client.id, user.id
    at_lock = asyncio.Event()
    original = AsyncSession.get

    async def get(self: AsyncSession, entity: Any, ident: Any, **kwargs: Any) -> Any:
        if entity is User and kwargs.get("with_for_update"):
            at_lock.set()
        return await original(self, entity, ident, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", get)
    try:
        async with sessions() as blocker:
            locked = await blocker.scalar(select(User).where(User.id == user_id).with_for_update())
            assert locked is not None

            async def recover() -> str:
                async with sessions() as db:
                    current_client = await db.get(ExternalApiClient, client_id)
                    assert current_client is not None
                    await db.commit()
                    try:
                        await recover_initial_credentials(
                            discord_id, request(), Response(), db, current_client
                        )
                    except ExternalApiError as exc:
                        return exc.code
                    return "unexpected-success"

            task = asyncio.create_task(recover())
            try:
                await asyncio.wait_for(at_lock.wait(), timeout=10)
                if mutation == "login":
                    locked.last_login_at = datetime.now(UTC)
                else:
                    locked.is_active = False
                await blocker.commit()
                assert await asyncio.wait_for(task, timeout=10) == "initial_credentials_unavailable"
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        async with sessions() as db:
            persisted = await db.get(User, user_id)
            assert persisted is not None and persisted.password_hash == "unchanged"
    finally:
        await cleanup(sessions, [client_id])
