"""Add durable READY retention and scheduler stop intent.

Revision ID: 20260831_22
Revises: 20260829_21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_22"
down_revision: str | None = "20260829_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "managed_torrents",
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_torrents",
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_torrents",
        sa.Column(
            "purge_stop_pending",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE managed_torrents AS mt
            SET ready_at = mt.updated_at,
                retention_expires_at = mt.updated_at +
                    CASE
                        WHEN (SELECT count(DISTINCT tr.user_id)
                              FROM torrent_requests AS tr
                              WHERE tr.managed_torrent_id = mt.id) >= 10
                            THEN interval '10 days'
                        WHEN (SELECT count(DISTINCT tr.user_id)
                              FROM torrent_requests AS tr
                              WHERE tr.managed_torrent_id = mt.id) >= 8
                            THEN interval '9 days'
                        WHEN (SELECT count(DISTINCT tr.user_id)
                              FROM torrent_requests AS tr
                              WHERE tr.managed_torrent_id = mt.id) >= 6
                            THEN interval '8 days'
                        WHEN (SELECT count(DISTINCT tr.user_id)
                              FROM torrent_requests AS tr
                              WHERE tr.managed_torrent_id = mt.id) >= 4
                            THEN interval '7 days'
                        WHEN (SELECT count(DISTINCT tr.user_id)
                              FROM torrent_requests AS tr
                              WHERE tr.managed_torrent_id = mt.id) >= 2
                            THEN interval '6 days'
                        ELSE interval '5 days'
                    END
            WHERE mt.state = 'READY'
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
    op.create_index(
        "ix_managed_torrents_purge_stop_pending",
        "managed_torrents",
        ["updated_at", "id"],
        postgresql_where=sa.text("purge_stop_pending"),
        sqlite_where=sa.text("purge_stop_pending = 1"),
    )
    op.alter_column("managed_torrents", "purge_stop_pending", server_default=None)


def downgrade() -> None:
    op.drop_index(
        "ix_managed_torrents_purge_stop_pending",
        table_name="managed_torrents",
    )
    op.drop_index("ix_managed_torrents_retention_due", table_name="managed_torrents")
    op.drop_constraint(
        "ck_managed_torrents_ready_retention",
        "managed_torrents",
        type_="check",
    )
    op.drop_column("managed_torrents", "purge_stop_pending")
    op.drop_column("managed_torrents", "retention_expires_at")
    op.drop_column("managed_torrents", "ready_at")
