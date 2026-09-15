"""Recover jobs failed by the C411 account routing migration.

Revision ID: 20260911_28
Revises: 20260911_27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_28"
down_revision: str | None = "20260911_27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD jobs failed before reading the staged payload, so replaying the same durable job is
    # safe. Restore PENDING first because the ADD handler intentionally rejects ERROR torrents.
    op.execute(
        sa.text(
            "WITH ranked_add AS ("
            "SELECT job.managed_torrent_id, job.last_error_code, "
            "row_number() OVER ("
            "PARTITION BY job.managed_torrent_id "
            "ORDER BY job.created_at DESC, job.id DESC"
            ") AS position "
            "FROM torrent_jobs AS job "
            "WHERE job.job_type = 'ADD_TORRENT' AND job.state = 'FAILED'"
            ") UPDATE managed_torrents AS torrent SET "
            "state = 'PENDING', retry_at = NULL, updated_at = CURRENT_TIMESTAMP "
            "FROM ranked_add "
            "WHERE torrent.id = ranked_add.managed_torrent_id "
            "AND torrent.state = 'ERROR' "
            "AND ranked_add.position = 1 "
            "AND ranked_add.last_error_code = 'torrent_account_route_invalid' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM torrent_jobs AS active_job "
            "WHERE active_job.managed_torrent_id = torrent.id "
            "AND active_job.job_type = 'ADD_TORRENT' "
            "AND active_job.state IN ('QUEUED', 'RUNNING')"
            ")"
        )
    )

    # Requeue only the newest failed job of each type. This avoids violating the partial unique
    # index for active SYNC jobs if several historical failures exist for one torrent. PURGE jobs
    # are replay-safe from PURGING and must resume so they do not strand qB/content data.
    op.execute(
        sa.text(
            "WITH ranked AS ("
            "SELECT job.id, job.managed_torrent_id, job.job_type, job.last_error_code, "
            "row_number() OVER ("
            "PARTITION BY job.managed_torrent_id, job.job_type "
            "ORDER BY job.created_at DESC, job.id DESC"
            ") AS position "
            "FROM torrent_jobs AS job "
            "JOIN managed_torrents AS torrent ON torrent.id = job.managed_torrent_id "
            "WHERE ((torrent.state IN ('PENDING', 'ERROR') "
            "AND job.job_type IN ('ADD_TORRENT', 'SYNC_TORRENT')) "
            "OR (torrent.state = 'PURGING' AND job.job_type = 'PURGE_TORRENT')) "
            "AND job.state = 'FAILED'"
            "), recoverable AS ("
            "SELECT ranked.id FROM ranked "
            "WHERE ranked.position = 1 "
            "AND ranked.last_error_code = 'torrent_account_route_invalid' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM torrent_jobs AS active_job "
            "WHERE active_job.managed_torrent_id = ranked.managed_torrent_id "
            "AND active_job.job_type = ranked.job_type "
            "AND active_job.state IN ('QUEUED', 'RUNNING')"
            ")"
            ") UPDATE torrent_jobs AS job SET "
            "state = 'QUEUED', attempt_count = 0, available_at = CURRENT_TIMESTAMP, "
            "timeout_at = NULL, claimed_by = NULL, claim_expires_at = NULL, "
            "cancel_requested_at = NULL, last_error_code = NULL, finished_at = NULL, "
            "updated_at = CURRENT_TIMESTAMP "
            "FROM recoverable WHERE job.id = recoverable.id"
        )
    )


def downgrade() -> None:
    # Recovery is a forward-only repair of durable jobs. Re-failing successfully replayed jobs
    # during rollback would corrupt valid runtime state.
    pass
