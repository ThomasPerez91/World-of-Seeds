"""Move READY retention authority to each user subscription.

Revision ID: 20260910_25
Revises: 20260909_24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_25"
down_revision: str | None = "20260909_24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "torrent_requests",
        sa.Column("unsubscribe_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_torrent_requests_unsubscribe_due",
        "torrent_requests",
        ["state", "unsubscribe_at", "id"],
        postgresql_where=sa.text("state = 'READY' AND unsubscribe_at IS NOT NULL"),
        sqlite_where=sa.text("state = 'READY' AND unsubscribe_at IS NOT NULL"),
    )
    # Existing rows are deliberately backfilled in bounded, replay-safe worker batches.
    op.drop_index("ix_managed_torrents_retention_due", table_name="managed_torrents")
    op.drop_constraint(
        "ck_managed_torrents_ready_retention",
        "managed_torrents",
        type_="check",
    )


def downgrade() -> None:
    op.create_check_constraint(
        "ck_managed_torrents_ready_retention",
        "managed_torrents",
        "(ready_at IS NULL AND retention_expires_at IS NULL) OR "
        "(ready_at IS NOT NULL AND retention_expires_at IS NOT NULL "
        "AND retention_expires_at >= ready_at)",
    )
    op.create_index(
        "ix_managed_torrents_retention_due",
        "managed_torrents",
        ["retention_expires_at", "id"],
        postgresql_where=sa.text("state = 'READY' AND retention_expires_at IS NOT NULL"),
        sqlite_where=sa.text("state = 'READY' AND retention_expires_at IS NOT NULL"),
    )
    op.drop_index("ix_torrent_requests_unsubscribe_due", table_name="torrent_requests")
    op.drop_column("torrent_requests", "unsubscribe_at")
