"""Add transactional V2 storage accounting.

Revision ID: 20260821_12
Revises: 20260821_11
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_12"
down_revision: str | None = "20260821_11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "storage_ledger",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("managed_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("disk_total_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("disk_free_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "pressure",
            sa.Enum(
                "NORMAL",
                "WARNING",
                "CRITICAL",
                name="storage_pressure_state",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="NORMAL",
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_storage_ledger_singleton"),
        sa.CheckConstraint(
            "managed_bytes >= 0 AND disk_total_bytes >= 0 AND disk_free_bytes >= 0 "
            "AND disk_free_bytes <= disk_total_bytes",
            name="ck_storage_ledger_non_negative",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "user_storage_usage",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("logical_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("logical_bytes >= 0", name="ck_user_storage_usage_non_negative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.execute(
        sa.text(
            "INSERT INTO storage_ledger "
            "(id, managed_bytes, disk_total_bytes, disk_free_bytes, pressure, updated_at) "
            "SELECT 1, COALESCE(SUM(total_size), 0), 0, 0, 'NORMAL', CURRENT_TIMESTAMP "
            "FROM managed_torrents WHERE state <> 'PURGED'"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO user_storage_usage (user_id, logical_bytes, updated_at) "
            "SELECT users.id, COALESCE(SUM(CASE WHEN torrent_requests.state IN "
            "('REQUESTED', 'ACTIVE', 'READY') THEN managed_torrents.total_size ELSE 0 END), 0), "
            "CURRENT_TIMESTAMP FROM users "
            "LEFT JOIN torrent_requests ON torrent_requests.user_id = users.id "
            "LEFT JOIN managed_torrents "
            "ON managed_torrents.id = torrent_requests.managed_torrent_id "
            "GROUP BY users.id"
        )
    )


def downgrade() -> None:
    op.drop_table("user_storage_usage")
    op.drop_table("storage_ledger")
