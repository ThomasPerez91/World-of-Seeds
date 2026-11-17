"""Exercise the timestamptz revision in an isolated PostgreSQL schema."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import make_url, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

STORED_UTC = "2026-11-17 10:00:00"
INSTANT = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def _revision_module() -> object:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("20261117_39")
    assert revision is not None
    assert revision.down_revision == "20261117_38"
    return revision.module


def migrate(connection: Connection, *, downgrade: bool = False) -> None:
    module = _revision_module()
    with Operations.context(MigrationContext.configure(connection)):
        if downgrade:
            module.downgrade()  # type: ignore[attr-defined]
        else:
            module.upgrade()  # type: ignore[attr-defined]


async def _column_types(connection: AsyncConnection, schema: str) -> dict[tuple[str, str], str]:
    rows = await connection.execute(
        text(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = :schema"
        ),
        {"schema": schema},
    )
    return {(table, column): data_type for table, column, data_type in rows}


@pytest.mark.asyncio
async def test_naive_utc_columns_become_timestamptz_without_moving_instants() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")
    columns: dict[str, tuple[str, ...]] = _revision_module().COLUMNS  # type: ignore[attr-defined]
    assert sum(len(names) for names in columns.values()) == 14

    # The same SELECT runs before and after the column types change: no cached plan may survive.
    engine = create_async_engine(
        make_url(database_url).update_query_dict({"prepared_statement_cache_size": "0"})
    )
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = f"timestamptz_migration_{uuid4().hex}"
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                # The session time zone must not shift the stored UTC instants.
                await connection.execute(text("SET LOCAL TimeZone TO 'Europe/Paris'"))
                for table, names in columns.items():
                    definitions = ", ".join(f'"{name}" timestamp' for name in names)
                    await connection.execute(
                        text(f'CREATE TABLE "{table}" (id integer PRIMARY KEY, {definitions})')
                    )
                    await connection.execute(
                        text(f'CREATE INDEX "ix_{table}_{names[0]}" ON "{table}" ("{names[0]}")')
                    )
                    values = ", ".join(f"'{STORED_UTC}'" for _ in names)
                    await connection.execute(text(f'INSERT INTO "{table}" VALUES (1, {values})'))

                await connection.run_sync(migrate)

                types = await _column_types(connection, schema)
                for table, names in columns.items():
                    for name in names:
                        assert types[(table, name)] == "timestamp with time zone"
                    row = (
                        (await connection.execute(text(f'SELECT * FROM "{table}"')))
                        .mappings()
                        .one()
                    )
                    assert all(row[name] == INSTANT for name in names), table

                await connection.run_sync(migrate, downgrade=True)

                types = await _column_types(connection, schema)
                for table, names in columns.items():
                    for name in names:
                        assert types[(table, name)] == "timestamp without time zone"
                    row = (
                        (await connection.execute(text(f'SELECT * FROM "{table}"')))
                        .mappings()
                        .one()
                    )
                    assert all(row[name] == INSTANT.replace(tzinfo=None) for name in names)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
