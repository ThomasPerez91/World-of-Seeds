"""Exercise the request budget default revision in an isolated PostgreSQL schema."""

import json
import os
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

KEY = "WOS_REQUEST_RATE_LIMIT_PER_MINUTE"


def migrate(connection: Connection, *, downgrade: bool = False) -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("20261117_38")
    assert revision is not None
    assert revision.down_revision == "20261022_37"
    with Operations.context(MigrationContext.configure(connection)):
        if downgrade:
            revision.module.downgrade()
        else:
            revision.module.upgrade()


async def _schema(connection: AsyncConnection, value: int | None) -> None:
    schema = f"request_rate_limit_migration_{uuid4().hex}"
    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    await connection.execute(
        text(
            "CREATE TABLE database_options ("
            "key varchar(128) PRIMARY KEY, integer_value bigint, version integer NOT NULL, "
            "updated_by_user_id uuid, updated_at timestamptz NOT NULL)"
        )
    )
    await connection.execute(
        text(
            "CREATE TABLE database_option_audits ("
            "id uuid PRIMARY KEY, option_key varchar(128) NOT NULL "
            "REFERENCES database_options(key), version integer NOT NULL, old_value json, "
            "new_value json NOT NULL, actor_user_id uuid, change_source varchar(32) NOT NULL, "
            "changed_at timestamptz NOT NULL, UNIQUE (option_key, version))"
        )
    )
    if value is not None:
        await connection.execute(
            text(
                "INSERT INTO database_options (key, integer_value, version, updated_at) "
                "VALUES (:key, :value, 1, '2026-08-21T00:00:00Z')"
            ),
            {"key": KEY, "value": value},
        )


async def _state(connection: AsyncConnection) -> tuple[object, list[tuple[object, ...]]]:
    option = (
        await connection.execute(
            text("SELECT integer_value, version FROM database_options WHERE key = :key"),
            {"key": KEY},
        )
    ).first()
    audits = (
        await connection.execute(
            text(
                "SELECT version, old_value::text, new_value::text, change_source, actor_user_id "
                "FROM database_option_audits ORDER BY version"
            )
        )
    ).all()
    return (tuple(option) if option is not None else None), [tuple(row) for row in audits]


def _database_url() -> str:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")
    return database_url


@pytest.mark.asyncio
async def test_untouched_default_is_raised_audited_and_reversible() -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await _schema(connection, 60)

                await connection.run_sync(migrate)
                option, audits = await _state(connection)
                assert option == (300, 2)
                assert len(audits) == 1
                version, old, new, source, actor = audits[0]
                assert (version, json.loads(str(old)), json.loads(str(new))) == (2, 60, 300)
                assert (source, actor) == ("migration", None)

                await connection.run_sync(migrate, downgrade=True)
                option, audits = await _state(connection)
                assert option == (60, 3)
                assert [row[0] for row in audits] == [2, 3]
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [120, None])
async def test_a_chosen_value_or_a_missing_row_is_left_alone(value: int | None) -> None:
    engine = create_async_engine(_database_url())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await _schema(connection, value)
                before = await _state(connection)

                await connection.run_sync(migrate)

                assert await _state(connection) == before
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
