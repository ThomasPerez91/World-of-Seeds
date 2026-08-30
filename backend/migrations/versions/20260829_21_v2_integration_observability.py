"""Add durable V2 integration health and bounded qB inventories.

Revision ID: 20260829_21
Revises: 20260829_20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_21"
down_revision: str | None = "20260829_20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_service_health",
        sa.Column("service", sa.String(length=32), nullable=False),
        sa.Column("account_ref", sa.Uuid(), nullable=False),
        sa.Column("observation_set", sa.Uuid(), nullable=False),
        sa.Column("account_count", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=11), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "service IN ('newgreedy', 'qbittorrent')",
            name="ck_integration_service_health_service",
        ),
        sa.CheckConstraint(
            "state IN ('HEALTHY', 'UNAVAILABLE')",
            name="integration_service_state",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_integration_service_health_latency",
        ),
        sa.CheckConstraint(
            "account_count BETWEEN 1 AND 16",
            name="ck_integration_service_health_account_count",
        ),
        sa.PrimaryKeyConstraint("service", "account_ref"),
    )
    op.create_table(
        "qbittorrent_inventory_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_ref", sa.Uuid(), nullable=False),
        sa.Column("observation_set", sa.Uuid(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("item_count >= 0", name="ck_qb_inventory_snapshot_item_count"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_qb_inventory_snapshot_account_checked",
        "qbittorrent_inventory_snapshots",
        ["account_ref", "checked_at"],
    )
    op.create_table(
        "qbittorrent_inventory_items",
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("info_hash", sa.String(length=40), nullable=False),
        sa.Column("storage_key", sa.Uuid(), nullable=True),
        sa.Column("claims_wos_identity", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "length(info_hash) = 40 AND info_hash = lower(info_hash)",
            name="ck_qb_inventory_items_hash",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["qbittorrent_inventory_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "info_hash"),
    )
    op.create_index(
        "ix_qb_inventory_items_hash",
        "qbittorrent_inventory_items",
        ["info_hash"],
    )
    op.create_index(
        "ix_qb_inventory_items_storage",
        "qbittorrent_inventory_items",
        ["storage_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_qb_inventory_items_storage", table_name="qbittorrent_inventory_items")
    op.drop_index("ix_qb_inventory_items_hash", table_name="qbittorrent_inventory_items")
    op.drop_table("qbittorrent_inventory_items")
    op.drop_index(
        "ix_qb_inventory_snapshot_account_checked",
        table_name="qbittorrent_inventory_snapshots",
    )
    op.drop_table("qbittorrent_inventory_snapshots")
    op.drop_table("integration_service_health")
