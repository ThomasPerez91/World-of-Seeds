"""Exercise the user login timestamp revision in an isolated PostgreSQL schema."""

import os
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine


def migrate(connection: Connection, *, downgrade: bool = False) -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("20260921_33")
    assert revision is not None
    with Operations.context(MigrationContext.configure(connection)):
        if downgrade:
            revision.module.downgrade()
        else:
            revision.module.upgrade()


@pytest.mark.asyncio
async def test_user_login_timestamp_migration_backfills_and_reverses() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = f"user_login_timestamp_migration_{uuid4().hex}"
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(
                    text(
                        "CREATE TABLE users (id uuid PRIMARY KEY, created_at timestamptz NOT NULL)"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TABLE user_sessions ("
                        "id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id), "
                        "created_at timestamptz NOT NULL)"
                    )
                )
                user_with_sessions = uuid4()
                user_without_session = uuid4()
                await connection.execute(
                    text(
                        "INSERT INTO users (id, created_at) VALUES "
                        "(:first, '2026-08-01T00:00:00Z'), "
                        "(:second, '2026-09-01T00:00:00Z')"
                    ),
                    {"first": user_with_sessions, "second": user_without_session},
                )
                await connection.execute(
                    text(
                        "INSERT INTO user_sessions (id, user_id, created_at) VALUES "
                        "(:first_session, :user_id, '2026-09-10T08:00:00Z'), "
                        "(:second_session, :user_id, '2026-09-20T18:30:00Z')"
                    ),
                    {
                        "first_session": uuid4(),
                        "second_session": uuid4(),
                        "user_id": user_with_sessions,
                    },
                )

                await connection.run_sync(migrate)
                rows = (
                    await connection.execute(
                        text("SELECT id, last_login_at FROM users ORDER BY created_at")
                    )
                ).all()
                assert rows[0].last_login_at.isoformat() == "2026-09-20T18:30:00+00:00"
                assert rows[1].last_login_at is None

                indexes = {
                    row.indexname
                    for row in (
                        await connection.execute(
                            text(
                                "SELECT indexname FROM pg_indexes "
                                "WHERE schemaname=:schema AND tablename='users'"
                            ),
                            {"schema": schema},
                        )
                    )
                }
                assert "ix_users_last_login_at" in indexes

                await connection.run_sync(lambda conn: migrate(conn, downgrade=True))
                columns = {
                    row.column_name
                    for row in (
                        await connection.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema=:schema AND table_name='users'"
                            ),
                            {"schema": schema},
                        )
                    )
                }
                assert columns == {"id", "created_at"}
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
