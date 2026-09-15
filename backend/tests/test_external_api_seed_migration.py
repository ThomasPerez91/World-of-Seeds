"""Exercise the 2.2.3 seed migration against existing PostgreSQL rows."""

import os
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine


def migrate(connection: Connection, *, downgrade: bool = False) -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("20260914_30")
    assert revision is not None
    with Operations.context(MigrationContext.configure(connection)):
        revision.module.downgrade() if downgrade else revision.module.upgrade()


@pytest.mark.asyncio
async def test_seed_migration_backfills_all_users_and_reverses() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = f"external_seed_migration_{uuid4().hex}"
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(
                    text("CREATE TABLE users (id uuid PRIMARY KEY, deleted_at timestamptz NULL)")
                )
                await connection.execute(
                    text(
                        "CREATE TABLE database_options (key varchar(128) PRIMARY KEY, "
                        "value_type varchar(16) NOT NULL, boolean_value boolean NULL, "
                        "integer_value bigint NULL, string_value varchar(512) NULL, "
                        "minimum_value bigint NULL, maximum_value bigint NULL, "
                        "choices json NOT NULL, "
                        "editable boolean NOT NULL, restart_required boolean NOT NULL, "
                        "version integer NOT NULL, updated_by_user_id uuid NULL, "
                        "created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL)"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TABLE database_option_audits (id uuid PRIMARY KEY, "
                        "option_key varchar(128) NOT NULL, version integer NOT NULL, "
                        "old_value json NULL, new_value json NOT NULL, actor_user_id uuid NULL, "
                        "change_source varchar(32) NOT NULL, changed_at timestamptz NOT NULL)"
                    )
                )
                active_id, deleted_id = uuid4(), uuid4()
                await connection.execute(
                    text("INSERT INTO users VALUES (:active, NULL), (:deleted, now())"),
                    {"active": active_id, "deleted": deleted_id},
                )
                await connection.run_sync(migrate)
                seed_rows = (
                    await connection.execute(text("SELECT id, auth_seed FROM users ORDER BY id"))
                ).all()
                seeds = [row.auth_seed for row in seed_rows]
                assert len(seeds) == 2
                assert len(set(seeds)) == 2
                assert all(len(seed) == 25 and seed.isalnum() for seed in seeds)
                assert (
                    await connection.scalar(
                        text(
                            "SELECT integer_value FROM database_options "
                            "WHERE key='WOS_MAX_USER_ACCOUNTS'"
                        )
                    )
                    == 100
                )
                with pytest.raises(IntegrityError):
                    async with connection.begin_nested():
                        await connection.execute(
                            text("UPDATE users SET auth_seed='short' WHERE id=:id"),
                            {"id": active_id},
                        )
                with pytest.raises(IntegrityError):
                    async with connection.begin_nested():
                        await connection.execute(
                            text("UPDATE users SET auth_seed=:seed WHERE id=:id"),
                            {
                                "id": active_id,
                                "seed": next(
                                    row.auth_seed for row in seed_rows if row.id != active_id
                                ),
                            },
                        )
                await connection.run_sync(lambda conn: migrate(conn, downgrade=True))
                assert list((await connection.execute(text("SELECT * FROM users"))).keys()) == [
                    "id",
                    "deleted_at",
                ]
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
