"""Coalesce active qBittorrent synchronization jobs.

Revision ID: 20260821_11
Revises: 20260821_10
Create Date: 2026-08-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260821_11"
down_revision: str | None = "20260821_10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_torrent_jobs_active_sync",
        "torrent_jobs",
        ["managed_torrent_id", "job_type"],
        unique=True,
        postgresql_where="job_type = 'SYNC_TORRENT' AND state IN ('QUEUED', 'RUNNING')",
    )


def downgrade() -> None:
    op.drop_index("uq_torrent_jobs_active_sync", table_name="torrent_jobs")
