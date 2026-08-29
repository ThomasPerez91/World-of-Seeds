"""Add the optional V1 import audit and rollback ledger.

Revision ID: 20260828_19
Revises: 20260827_18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_19"
down_revision: str | None = "20260827_18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "v1_import_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_snapshot_id", sa.String(length=128), nullable=False),
        sa.Column("backup_id", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "APPLIED",
                "ROLLED_BACK",
                name="v1_import_run_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source_rows", sa.Integer(), nullable=False),
        sa.Column("created_torrents", sa.Integer(), nullable=False),
        sa.Column("created_requests", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(source_fingerprint) = 64", name="ck_v1_import_fingerprint"),
        sa.CheckConstraint(
            "source_rows >= 0 AND created_torrents >= 0 AND created_requests >= 0",
            name="ck_v1_import_counts",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_fingerprint", name="uq_v1_import_source_fingerprint"),
    )
    op.create_index("ix_v1_import_runs_created", "v1_import_runs", ["created_at"])
    op.create_table(
        "v1_import_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("target_managed_torrent_id", sa.Uuid(), nullable=False),
        sa.Column("target_request_id", sa.Uuid(), nullable=False),
        sa.Column("managed_torrent_created", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["v1_import_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_record_id", name="uq_v1_import_run_source_record"),
    )
    op.create_index("ix_v1_import_items_run", "v1_import_items", ["run_id"])
    op.create_index("ix_v1_import_items_request", "v1_import_items", ["target_request_id"])


def downgrade() -> None:
    op.drop_index("ix_v1_import_items_request", table_name="v1_import_items")
    op.drop_index("ix_v1_import_items_run", table_name="v1_import_items")
    op.drop_table("v1_import_items")
    op.drop_index("ix_v1_import_runs_created", table_name="v1_import_runs")
    op.drop_table("v1_import_runs")
