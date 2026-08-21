"""Add secret-safe V2 tracker activities and opaque account references.

Revision ID: 20260821_09
Revises: 20260821_08
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_09"
down_revision: str | Sequence[str] | None = "20260821_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "managed_torrents",
        sa.Column("tracker_account_ref", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "managed_torrents",
        sa.Column("qbittorrent_account_ref", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_managed_torrents_tracker_account",
        "managed_torrents",
        ["tracker_account_ref"],
    )
    op.create_index(
        "ix_managed_torrents_qb_account",
        "managed_torrents",
        ["qbittorrent_account_ref"],
    )
    op.create_table(
        "tracker_activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.Uuid(), nullable=False),
        sa.Column("managed_torrent_id", sa.Uuid(), nullable=False),
        sa.Column("tracker_account_ref", sa.Uuid(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "ANNOUNCE",
                "SCRAPE",
                "PROXY_HEALTH",
                "TRACKER_STATUS",
                name="tracker_activity_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(
                "SUCCESS",
                "DEGRADED",
                "FAILED",
                name="tracker_activity_outcome",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "diagnostic_code",
            sa.Enum(
                "TIMEOUT",
                "UNAVAILABLE",
                "AUTHENTICATION_FAILED",
                "TRACKER_REJECTED",
                "RATE_LIMITED",
                "INVALID_RESPONSE",
                "UNKNOWN_ERROR",
                name="tracker_diagnostic_code",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(outcome = 'SUCCESS' AND diagnostic_code IS NULL) "
            "OR (outcome IN ('DEGRADED', 'FAILED') AND diagnostic_code IS NOT NULL)",
            name="ck_tracker_activities_diagnostic",
        ),
        sa.ForeignKeyConstraint(
            ["managed_torrent_id"],
            ["managed_torrents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_tracker_activities_event_key"),
    )
    op.create_index(
        "ix_tracker_activities_torrent_occurred",
        "tracker_activities",
        ["managed_torrent_id", "occurred_at"],
    )
    op.create_index(
        "ix_tracker_activities_account_occurred",
        "tracker_activities",
        ["tracker_account_ref", "occurred_at"],
    )
    op.create_index(
        "ix_tracker_activities_outcome_occurred",
        "tracker_activities",
        ["outcome", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tracker_activities_outcome_occurred", table_name="tracker_activities")
    op.drop_index("ix_tracker_activities_account_occurred", table_name="tracker_activities")
    op.drop_index("ix_tracker_activities_torrent_occurred", table_name="tracker_activities")
    op.drop_table("tracker_activities")
    op.drop_index("ix_managed_torrents_qb_account", table_name="managed_torrents")
    op.drop_index("ix_managed_torrents_tracker_account", table_name="managed_torrents")
    op.drop_column("managed_torrents", "qbittorrent_account_ref")
    op.drop_column("managed_torrents", "tracker_account_ref")
