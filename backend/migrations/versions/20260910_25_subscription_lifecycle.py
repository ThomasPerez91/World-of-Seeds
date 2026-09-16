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
    # Rebuild the legacy physical-retention deadline before restoring its check
    # constraint. Torrents that became READY after this revision have a
    # per-subscription deadline but deliberately no managed-torrent deadline.
    op.execute(
        sa.text(
            """
            WITH subscription_deadlines AS (
                SELECT
                    managed_torrent_id,
                    min(ready_at) FILTER (WHERE ready_at IS NOT NULL) AS ready_at,
                    max(unsubscribe_at) FILTER (WHERE unsubscribe_at IS NOT NULL)
                        AS unsubscribe_at
                FROM torrent_requests
                WHERE state = 'READY'
                GROUP BY managed_torrent_id
            )
            UPDATE managed_torrents AS mt
            SET ready_at = COALESCE(
                    mt.ready_at,
                    deadlines.ready_at,
                    deadlines.unsubscribe_at,
                    mt.updated_at
                ),
                retention_expires_at = GREATEST(
                    COALESCE(
                        mt.ready_at,
                        deadlines.ready_at,
                        deadlines.unsubscribe_at,
                        mt.updated_at
                    ),
                    COALESCE(
                        mt.retention_expires_at,
                        mt.ready_at,
                        deadlines.ready_at,
                        deadlines.unsubscribe_at,
                        mt.updated_at
                    ),
                    COALESCE(
                        deadlines.unsubscribe_at,
                        mt.retention_expires_at,
                        mt.ready_at,
                        deadlines.ready_at,
                        mt.updated_at
                    )
                )
            FROM subscription_deadlines AS deadlines
            WHERE deadlines.managed_torrent_id = mt.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE managed_torrents
            SET ready_at = COALESCE(ready_at, retention_expires_at),
                retention_expires_at = COALESCE(retention_expires_at, ready_at)
            WHERE ready_at IS NOT NULL OR retention_expires_at IS NOT NULL
            """
        )
    )
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
