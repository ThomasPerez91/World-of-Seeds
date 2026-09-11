"""Exercise the C411 routing incident recovery against PostgreSQL."""

import os
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

ADD_RECOVERED = UUID("10000000-0000-0000-0000-000000000001")
SYNC_RECOVERED = UUID("10000000-0000-0000-0000-000000000002")
STALE_FAILURE = UUID("10000000-0000-0000-0000-000000000003")
ACTIVE_EXISTS = UUID("10000000-0000-0000-0000-000000000004")
PURGE_RECOVERED = UUID("10000000-0000-0000-0000-000000000005")


def migrate(connection: Connection) -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("20260911_28")
    assert revision is not None
    with Operations.context(MigrationContext.configure(connection)):
        revision.module.upgrade()


@pytest.mark.asyncio
async def test_route_recovery_requeues_only_current_routing_failures() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL migration test requires WOS_DATABASE_URL")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                schema = f"c411_route_recovery_{uuid4().hex}"
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                await connection.execute(
                    text(
                        "CREATE TABLE managed_torrents ("
                        "id uuid PRIMARY KEY, state text NOT NULL, retry_at timestamp NULL, "
                        "updated_at timestamp NOT NULL)"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE TABLE torrent_jobs ("
                        "id uuid PRIMARY KEY, managed_torrent_id uuid NOT NULL, "
                        "job_type text NOT NULL, state text NOT NULL, "
                        "attempt_count integer NOT NULL, "
                        "max_attempts integer NOT NULL, available_at timestamp NOT NULL, "
                        "timeout_at timestamp NULL, claimed_by text NULL, "
                        "claim_expires_at timestamp NULL, cancel_requested_at timestamp NULL, "
                        "last_error_code text NULL, finished_at timestamp NULL, "
                        "created_at timestamp NOT NULL, updated_at timestamp NOT NULL)"
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO managed_torrents VALUES "
                        "(:add, 'ERROR', now(), now()), "
                        "(:sync, 'ERROR', now(), now()), "
                        "(:stale, 'ERROR', now(), now()), "
                        "(:active, 'ERROR', now(), now()), "
                        "(:purge, 'PURGING', now(), now())"
                    ),
                    {
                        "add": ADD_RECOVERED,
                        "sync": SYNC_RECOVERED,
                        "stale": STALE_FAILURE,
                        "active": ACTIVE_EXISTS,
                        "purge": PURGE_RECOVERED,
                    },
                )

                route_error = "torrent_account_route_invalid"
                jobs = [
                    (uuid4(), ADD_RECOVERED, "ADD_TORRENT", "FAILED", route_error, 1),
                    (uuid4(), SYNC_RECOVERED, "SYNC_TORRENT", "FAILED", route_error, 1),
                    (uuid4(), STALE_FAILURE, "ADD_TORRENT", "FAILED", route_error, 1),
                    (uuid4(), STALE_FAILURE, "ADD_TORRENT", "FAILED", "payload_invalid", 2),
                    (uuid4(), ACTIVE_EXISTS, "SYNC_TORRENT", "FAILED", route_error, 1),
                    (uuid4(), ACTIVE_EXISTS, "SYNC_TORRENT", "QUEUED", None, 2),
                    (uuid4(), PURGE_RECOVERED, "PURGE_TORRENT", "FAILED", route_error, 1),
                ]
                for job_id, torrent_id, job_type, state, error_code, minute in jobs:
                    await connection.execute(
                        text(
                            "INSERT INTO torrent_jobs VALUES ("
                            ":id, :torrent_id, :job_type, :state, 3, 5, now(), NULL, NULL, NULL, "
                            "NULL, :error_code, "
                            "CASE WHEN :state = 'FAILED' THEN now() ELSE NULL END, "
                            "timestamp '2026-09-11 10:00:00' "
                            "+ :minute * interval '1 minute', now())"
                        ),
                        {
                            "id": job_id,
                            "torrent_id": torrent_id,
                            "job_type": job_type,
                            "state": state,
                            "error_code": error_code,
                            "minute": minute,
                        },
                    )

                await connection.run_sync(migrate)

                torrent_states: dict[UUID, str] = {}
                state_rows = (
                    await connection.execute(
                        text("SELECT id, state FROM managed_torrents ORDER BY id")
                    )
                ).all()
                for row in state_rows:
                    torrent_states[row[0]] = row[1]
                assert torrent_states == {
                    ADD_RECOVERED: "PENDING",
                    SYNC_RECOVERED: "ERROR",
                    STALE_FAILURE: "ERROR",
                    ACTIVE_EXISTS: "ERROR",
                    PURGE_RECOVERED: "PURGING",
                }
                recovered_jobs = set(
                    await connection.execute(
                        text(
                            "SELECT managed_torrent_id, job_type FROM torrent_jobs "
                            "WHERE state = 'QUEUED' AND attempt_count = 0 "
                            "AND last_error_code IS NULL AND finished_at IS NULL"
                        )
                    )
                )
                assert {tuple(row) for row in recovered_jobs} == {
                    (ADD_RECOVERED, "ADD_TORRENT"),
                    (SYNC_RECOVERED, "SYNC_TORRENT"),
                    (PURGE_RECOVERED, "PURGE_TORRENT"),
                }
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM torrent_jobs "
                            "WHERE managed_torrent_id = :torrent_id AND state = 'FAILED'"
                        ),
                        {"torrent_id": STALE_FAILURE},
                    )
                    == 2
                )
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM torrent_jobs "
                            "WHERE managed_torrent_id = :torrent_id "
                            "AND state = 'FAILED' AND last_error_code = :error_code"
                        ),
                        {"torrent_id": ACTIVE_EXISTS, "error_code": route_error},
                    )
                    == 1
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
