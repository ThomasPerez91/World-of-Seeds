"""Exercise the subscription lifecycle rollback with post-migration data."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

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
    revision = script.get_revision("20260910_25")
    assert revision is not None
    with Operations.context(MigrationContext.configure(connection)):
        if downgrade:
            revision.module.downgrade()
        else:
            revision.module.upgrade()


@pytest.mark.asyncio
async def test_subscription_lifecycle_downgrade_rebuilds_legacy_retention_deadlines() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = f"subscription_lifecycle_{uuid4().hex}"
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(
                    text(
                        """
                        CREATE TABLE managed_torrents (
                            id uuid PRIMARY KEY,
                            state varchar(32) NOT NULL,
                            ready_at timestamptz NULL,
                            retention_expires_at timestamptz NULL,
                            updated_at timestamptz NOT NULL
                        )
                        """
                    )
                )
                await connection.execute(
                    text(
                        """
                        ALTER TABLE managed_torrents
                        ADD CONSTRAINT ck_managed_torrents_ready_retention CHECK (
                            (ready_at IS NULL AND retention_expires_at IS NULL) OR
                            (ready_at IS NOT NULL AND retention_expires_at IS NOT NULL
                             AND retention_expires_at >= ready_at)
                        )
                        """
                    )
                )
                await connection.execute(
                    text(
                        """
                        CREATE INDEX ix_managed_torrents_retention_due
                        ON managed_torrents (retention_expires_at, id)
                        WHERE state = 'READY' AND retention_expires_at IS NOT NULL
                        """
                    )
                )
                await connection.execute(
                    text(
                        """
                        CREATE TABLE torrent_requests (
                            id uuid PRIMARY KEY,
                            managed_torrent_id uuid NOT NULL REFERENCES managed_torrents(id),
                            state varchar(32) NOT NULL,
                            ready_at timestamptz NULL
                        )
                        """
                    )
                )
                await connection.run_sync(migrate)

                ready_at = datetime(2030, 8, 1, 12, tzinfo=UTC)
                unsubscribe_at = ready_at + timedelta(hours=48)
                ready_torrent_id = uuid4()
                grace_torrent_id = uuid4()
                repaired_torrent_id = uuid4()
                await connection.execute(
                    text(
                        """
                        INSERT INTO managed_torrents
                            (id, state, ready_at, retention_expires_at, updated_at)
                        VALUES
                            (:ready_id, 'READY', :ready_at, NULL, :ready_at),
                            (:grace_id, 'PURGE_PENDING', :ready_at, NULL, :ready_at),
                            (:repaired_id, 'READY', NULL, NULL, :ready_at)
                        """
                    ),
                    {
                        "ready_id": ready_torrent_id,
                        "grace_id": grace_torrent_id,
                        "repaired_id": repaired_torrent_id,
                        "ready_at": ready_at,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO torrent_requests
                            (id, managed_torrent_id, state, ready_at, unsubscribe_at)
                        VALUES
                            (:ready_request_id, :ready_id, 'READY', :ready_at, :unsubscribe_at),
                            (
                                :repaired_request_id,
                                :repaired_id,
                                'READY',
                                :ready_at,
                                :unsubscribe_at
                            )
                        """
                    ),
                    {
                        "ready_request_id": uuid4(),
                        "repaired_request_id": uuid4(),
                        "ready_id": ready_torrent_id,
                        "repaired_id": repaired_torrent_id,
                        "ready_at": ready_at,
                        "unsubscribe_at": unsubscribe_at,
                    },
                )

                await connection.run_sync(lambda conn: migrate(conn, downgrade=True))

                rows = {
                    UUID(str(row.id)): (row.ready_at, row.retention_expires_at)
                    for row in (
                        await connection.execute(
                            text(
                                """
                                SELECT id, ready_at, retention_expires_at
                                FROM managed_torrents
                                ORDER BY id
                                """
                            )
                        )
                    )
                }
                assert rows[ready_torrent_id] == (ready_at, unsubscribe_at)
                assert rows[grace_torrent_id] == (ready_at, ready_at)
                assert rows[repaired_torrent_id] == (ready_at, unsubscribe_at)
                assert (
                    await connection.scalar(
                        text(
                            """
                            SELECT count(*)
                            FROM information_schema.columns
                            WHERE table_schema = :schema
                              AND table_name = 'torrent_requests'
                              AND column_name = 'unsubscribe_at'
                            """
                        ),
                        {"schema": schema},
                    )
                    == 0
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
