import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_storage_admission_concurrency import postgres_storage as postgres_storage

from app.auth import password_reset as recovery
from app.auth.security import hash_password, hash_token, verify_password
from app.models import DiscordAccountLink, ExternalApiAudit, ExternalApiClient, PasswordReset, User


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redeem", "issue", "expired_during_hash"])
async def test_recovery_serialization(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    sessions, prefix = postgres_storage
    raw = uuid4().hex
    discord_id = str(100000000000000000 + uuid4().int % 900000000000000000)
    async with sessions() as db, db.begin():
        user = User(username=prefix, password_hash=hash_password("original-password"))
        issuer = ExternalApiClient(
            name=prefix, key_hash=hash_token(raw), key_prefix="test", scopes=[recovery.RESET_SCOPE]
        )
        db.add_all([user, issuer])
        await db.flush()
        db.add(DiscordAccountLink(discord_user_id=discord_id, user_id=user.id, client_id=issuer.id))
    user_id, client_id = user.id, issuer.id
    try:
        if mode == "issue":

            async def issue() -> str:
                async with sessions() as db:
                    try:
                        await recovery.issue_password_reset(
                            db,
                            client_id=client_id,
                            key_hash=hash_token(raw),
                            discord_id=discord_id,
                            request_id=uuid4().hex,
                        )
                        return "ok"
                    except recovery.ResetThrottled:
                        await db.rollback()
                        return "throttled"

            assert sorted(await asyncio.gather(issue(), issue())) == ["ok", "throttled"]
            return
        async with sessions() as db:
            token = await recovery.issue_password_reset(
                db,
                client_id=client_id,
                key_hash=hash_token(raw),
                discord_id=discord_id,
                request_id=uuid4().hex,
            )
        if mode == "expired_during_hash":
            started, release = asyncio.Event(), asyncio.Event()
            encoded = hash_password("replacement-password")

            async def hash_later(value: str) -> str:
                started.set()
                await release.wait()
                return encoded

            monkeypatch.setattr(recovery, "hash_password_async", hash_later)
            async with sessions() as db:
                task = asyncio.create_task(
                    recovery.confirm_password_reset(
                        db, token=token, password="replacement-password", request_id=uuid4().hex
                    )
                )
                await asyncio.wait_for(started.wait(), 2)
                assert not db.in_transaction()
                async with sessions() as other, other.begin():
                    grant = await other.get(PasswordReset, user_id, with_for_update=True)
                    assert grant is not None
                    grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                release.set()
                with pytest.raises(recovery.ResetUnavailable):
                    await task
                await db.rollback()
            return

        async def redeem() -> str:
            async with sessions() as db:
                try:
                    await recovery.confirm_password_reset(
                        db, token=token, password="replacement-password", request_id=uuid4().hex
                    )
                    return "ok"
                except recovery.ResetUnavailable:
                    await db.rollback()
                    return "used"

        assert sorted(await asyncio.gather(redeem(), redeem())) == ["ok", "used"]
        async with sessions() as db:
            current = await db.get(User, user_id)
            assert current is not None and verify_password(
                "replacement-password", current.password_hash
            )
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(ExternalApiAudit)
                    .where(
                        ExternalApiAudit.user_id == user_id,
                        ExternalApiAudit.action == "password_reset.complete",
                    )
                )
                == 1
            )
    finally:
        async with sessions() as db, db.begin():
            await db.execute(delete(PasswordReset).where(PasswordReset.user_id == user_id))
            await db.execute(
                delete(ExternalApiAudit).where(ExternalApiAudit.client_id == client_id)
            )
            await db.execute(
                delete(DiscordAccountLink).where(DiscordAccountLink.user_id == user_id)
            )
            await db.execute(delete(ExternalApiClient).where(ExternalApiClient.id == client_id))
