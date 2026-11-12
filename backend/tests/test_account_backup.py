import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.account_backup import BackupError, Snapshot, export_accounts, restore_accounts
from app.auth.security import hash_password, verify_password
from app.models import Base


@pytest.mark.asyncio
async def test_accounts_round_trip_into_fresh_postgres_schema() -> None:
    url = os.environ.get("WOS_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("Requires isolated PostgreSQL CI service")
    engine = create_async_engine(url)
    source, target = (f"account_backup_{uuid4().hex}" for _ in range(2))
    users = Base.metadata.tables["users"]
    links = Base.metadata.tables["discord_account_links"]
    clients = Base.metadata.tables["external_api_clients"]
    user_id, old_client, new_client = uuid4(), uuid4(), uuid4()
    password = "test-account-backup-password"
    try:
        async with engine.begin() as conn:
            for schema, client_id in ((source, old_client), (target, new_client)):
                await conn.execute(text(f"CREATE SCHEMA {schema}"))
                await conn.execute(text(f"SET LOCAL search_path TO {schema}"))
                for table in (users, clients, links):
                    await conn.run_sync(table.create)
                await conn.execute(
                    clients.insert().values(
                        id=client_id, name="test-bot", key_hash="a" * 64, key_prefix="test"
                    )
                )
            await conn.execute(text(f"SET LOCAL search_path TO {source}"))
            await conn.execute(
                users.insert().values(
                    id=user_id, username="backup-test", password_hash=hash_password(password)
                )
            )
            await conn.execute(
                links.insert().values(
                    discord_user_id="123456789012345678",
                    user_id=user_id,
                    client_id=old_client,
                    created_at=datetime.now(UTC),
                )
            )
        async with engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL search_path TO {source}"))
            snapshot = await export_accounts(conn)
        snapshot = Snapshot.model_validate_json(snapshot.model_dump_json())
        async with engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL search_path TO {target}"))
            await restore_accounts(conn, snapshot, {old_client: new_client})
        async with engine.begin() as conn:
            await conn.execute(text(f"SET LOCAL search_path TO {target}"))
            restored_hash = (await conn.execute(select(users.c.password_hash))).scalar_one()
            assert verify_password(password, restored_hash)
            assert (await conn.execute(select(links.c.client_id))).scalar_one() == new_client
            with pytest.raises(BackupError, match="empty users"):
                await restore_accounts(conn, snapshot, {old_client: new_client})
    finally:
        async with engine.begin() as conn:
            for schema in (source, target):
                await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await engine.dispose()
