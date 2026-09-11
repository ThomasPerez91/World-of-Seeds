"""Exercise the grace-period lifecycle revision against PostgreSQL."""

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
    revision = script.get_revision("20260911_26")
    assert revision is not None
    with Operations.context(MigrationContext.configure(connection)):
        if downgrade:
            revision.module.downgrade()
        else:
            revision.module.upgrade()


@pytest.mark.asyncio
async def test_grace_period_migration_allows_normal_state_deadline_and_reverses() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = f"grace_period_migration_{uuid4().hex}"
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(
                    text(
                        "CREATE TABLE managed_torrents ("
                        "id integer PRIMARY KEY, state text NOT NULL, "
                        "purge_after timestamptz NULL, lifecycle_generation integer NOT NULL, "
                        "desired_active boolean NOT NULL, desired_priority integer NULL, "
                        "desired_download_limit bigint NOT NULL, "
                        "purge_stop_pending boolean NOT NULL, "
                        "CONSTRAINT ck_managed_torrents_lifecycle CHECK ("
                        "lifecycle_generation >= 0 AND "
                        "((state IN ('PURGE_PENDING', 'PURGING') "
                        "AND purge_after IS NOT NULL) OR "
                        "(state NOT IN ('PURGE_PENDING', 'PURGING') "
                        "AND purge_after IS NULL))))"
                    )
                )
                await connection.run_sync(migrate)
                await connection.execute(
                    text(
                        "INSERT INTO managed_torrents VALUES "
                        "(1, 'READY', now(), 1, false, NULL, 0, false), "
                        "(2, 'DOWNLOADING', now(), 1, true, 0, 1024, false)"
                    )
                )
                with pytest.raises(IntegrityError):
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                "INSERT INTO managed_torrents VALUES "
                                "(3, 'PURGING', NULL, 1, false, NULL, 0, false)"
                            )
                        )

                await connection.run_sync(lambda conn: migrate(conn, downgrade=True))
                rows = (
                    await connection.execute(
                        text(
                            "SELECT state, desired_active, desired_priority, "
                            "desired_download_limit, purge_stop_pending "
                            "FROM managed_torrents ORDER BY id"
                        )
                    )
                ).all()
                assert [tuple(row) for row in rows] == [
                    ("PURGE_PENDING", False, None, 0, True),
                    ("PURGE_PENDING", False, None, 0, True),
                ]
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
