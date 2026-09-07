"""Add durable V2 torrent jobs.

Revision ID: 20260821_07
Revises: 20260821_06
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_07"
down_revision: str | Sequence[str] | None = "20260821_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "torrent_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("managed_torrent_id", sa.Uuid(), nullable=False),
        sa.Column("torrent_request_id", sa.Uuid(), nullable=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "QUEUED",
                "RUNNING",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
                name="torrent_job_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(job_type) BETWEEN 1 AND 64", name="ck_torrent_jobs_type"),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="ck_torrent_jobs_key",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_torrent_jobs_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_torrent_jobs_max_attempts"),
        sa.CheckConstraint(
            "state <> 'QUEUED' OR attempt_count < max_attempts",
            name="ck_torrent_jobs_queued_attempts",
        ),
        sa.CheckConstraint(
            "(state = 'RUNNING' AND claimed_by IS NOT NULL "
            "AND claim_expires_at IS NOT NULL AND timeout_at IS NOT NULL) "
            "OR (state <> 'RUNNING' AND claimed_by IS NULL "
            "AND claim_expires_at IS NULL AND timeout_at IS NULL)",
            name="ck_torrent_jobs_claim",
        ),
        sa.CheckConstraint(
            "(state IN ('COMPLETED', 'FAILED', 'CANCELLED') AND finished_at IS NOT NULL) "
            "OR (state IN ('QUEUED', 'RUNNING') AND finished_at IS NULL)",
            name="ck_torrent_jobs_finished",
        ),
        sa.ForeignKeyConstraint(
            ["managed_torrent_id"], ["managed_torrents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["torrent_request_id"], ["torrent_requests.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_torrent_jobs_claimable",
        "torrent_jobs",
        ["state", "available_at", "created_at"],
    )
    op.create_index(
        "ix_torrent_jobs_request",
        "torrent_jobs",
        ["torrent_request_id"],
    )
    op.create_index(
        "ix_torrent_jobs_torrent_created",
        "torrent_jobs",
        ["managed_torrent_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_torrent_jobs_torrent_created", table_name="torrent_jobs")
    op.drop_index("ix_torrent_jobs_request", table_name="torrent_jobs")
    op.drop_index("ix_torrent_jobs_claimable", table_name="torrent_jobs")
    op.drop_table("torrent_jobs")
