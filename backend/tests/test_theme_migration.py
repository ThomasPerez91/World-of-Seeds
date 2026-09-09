"""Exercise the real revision in an isolated transactional PostgreSQL schema."""

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
    revision = script.get_revision("20260908_23")
    assert revision is not None
    with Operations.context(MigrationContext.configure(connection)):
        if downgrade:
            revision.module.downgrade()
        else:
            revision.module.upgrade()


@pytest.mark.asyncio
async def test_theme_migration_preserves_existing_users_and_reverses_cleanly() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = f"theme_migration_{uuid4().hex}"
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(text("CREATE TABLE users (id integer PRIMARY KEY)"))
                await connection.execute(text("INSERT INTO users VALUES (1)"))
                await connection.run_sync(migrate)
                assert (
                    await connection.scalar(text("SELECT preferred_theme FROM users WHERE id=1"))
                    == "system"
                )
                for theme in ("light", "dark", "system"):
                    await connection.execute(
                        text("UPDATE users SET preferred_theme=:theme"), {"theme": theme}
                    )
                    assert (
                        await connection.scalar(text("SELECT preferred_theme FROM users")) == theme
                    )
                for invalid in ("auto", None):
                    with pytest.raises(IntegrityError):
                        async with connection.begin_nested():
                            await connection.execute(
                                text("UPDATE users SET preferred_theme=:theme"), {"theme": invalid}
                            )
                await connection.run_sync(lambda conn: migrate(conn, downgrade=True))
                assert list((await connection.execute(text("SELECT * FROM users"))).keys()) == [
                    "id"
                ]
                assert await connection.scalar(text("SELECT id FROM users")) == 1
                await connection.run_sync(migrate)
                await connection.execute(text("INSERT INTO users (id) VALUES (2)"))
                assert (
                    await connection.scalar(text("SELECT preferred_theme FROM users WHERE id=2"))
                    == "system"
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
