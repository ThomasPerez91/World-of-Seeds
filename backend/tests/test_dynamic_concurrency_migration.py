"""Exercise the dynamic-concurrency revision in an isolated PostgreSQL schema."""

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
    revision = script.get_revision("20260921_32")
    assert revision is not None
    with Operations.context(MigrationContext.configure(connection)):
        if downgrade:
            revision.module.downgrade()
        else:
            revision.module.upgrade()


@pytest.mark.asyncio
async def test_dynamic_concurrency_migration_preserves_state_and_reverses() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = f"dynamic_concurrency_migration_{uuid4().hex}"
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(
                    text(
                        "CREATE TABLE scheduler_state ("
                        "id integer PRIMARY KEY, updated_at timestamp NOT NULL)"
                    )
                )
                await connection.execute(
                    text("INSERT INTO scheduler_state VALUES (1, CURRENT_TIMESTAMP)")
                )
                await connection.run_sync(migrate)
                row = (
                    await connection.execute(
                        text(
                            "SELECT dynamic_current_active, dynamic_sample_baseline, "
                            "dynamic_last_decision FROM scheduler_state WHERE id=1"
                        )
                    )
                ).one()
                assert tuple(row) == (2, {}, "dynamic_idle_reset")
                await connection.execute(
                    text(
                        "UPDATE scheduler_state SET dynamic_current_active=4, "
                        "dynamic_observed_bytes_per_second=240000000, "
                        "dynamic_active_count=4, dynamic_waiting_count=3"
                    )
                )
                await connection.run_sync(lambda conn: migrate(conn, downgrade=True))
                columns = {
                    row.column_name
                    for row in (
                        await connection.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema=:schema AND table_name='scheduler_state'"
                            ),
                            {"schema": schema},
                        )
                    )
                }
                assert columns == {"id", "updated_at"}
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
