"""Exercise the new revision against existing accounts in isolated PostgreSQL DDL."""

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


def migrate(connection: Connection, revision_id: str, *, downgrade: bool = False) -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision(revision_id)
    assert revision is not None
    with Operations.context(MigrationContext.configure(connection)):
        if downgrade:
            revision.module.downgrade()
        else:
            revision.module.upgrade()


@pytest.mark.asyncio
async def test_drop_theme_migration_preserves_accounts_and_restores_legacy_schema() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = f"drop_theme_migration_{uuid4().hex}"
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(text("CREATE TABLE users (id integer PRIMARY KEY)"))
                await connection.run_sync(lambda conn: migrate(conn, "20260908_23"))
                for index, theme in enumerate(("light", "dark", "system"), start=1):
                    await connection.execute(
                        text("INSERT INTO users (id, preferred_theme) VALUES (:id, :theme)"),
                        {"id": index, "theme": theme},
                    )

                await connection.run_sync(lambda conn: migrate(conn, "20260925_34"))
                assert list((await connection.execute(text("SELECT * FROM users"))).keys()) == [
                    "id"
                ]
                assert (
                    await connection.scalars(text("SELECT id FROM users ORDER BY id"))
                ).all() == [1, 2, 3]
                await connection.execute(text("INSERT INTO users (id) VALUES (4)"))

                await connection.run_sync(lambda conn: migrate(conn, "20260925_34", downgrade=True))
                assert (
                    await connection.scalars(text("SELECT preferred_theme FROM users ORDER BY id"))
                ).all() == ["system"] * 4
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
