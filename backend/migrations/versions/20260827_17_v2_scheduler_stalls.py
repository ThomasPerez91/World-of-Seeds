"""Add durable V2 scheduler stall observations.

Revision ID: 20260827_17
Revises: 20260821_16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_17"
down_revision: str | None = "20260821_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("managed_torrents", sa.Column("last_progress_at", sa.DateTime(), nullable=True))
    op.add_column(
        "managed_torrents", sa.Column("last_downloaded_bytes", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "managed_torrents",
        sa.Column("stall_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("managed_torrents", sa.Column("scheduler_retry_at", sa.DateTime(), nullable=True))
    op.create_check_constraint(
        "ck_managed_torrents_stall_values",
        "managed_torrents",
        "(last_downloaded_bytes IS NULL OR last_downloaded_bytes >= 0) AND stall_count >= 0",
    )
    op.create_index(
        "ix_managed_torrents_scheduler_retry",
        "managed_torrents",
        ["state", "scheduler_retry_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_managed_torrents_scheduler_retry", table_name="managed_torrents")
    op.drop_constraint("ck_managed_torrents_stall_values", "managed_torrents", type_="check")
    op.drop_column("managed_torrents", "scheduler_retry_at")
    op.drop_column("managed_torrents", "stall_count")
    op.drop_column("managed_torrents", "last_downloaded_bytes")
    op.drop_column("managed_torrents", "last_progress_at")
