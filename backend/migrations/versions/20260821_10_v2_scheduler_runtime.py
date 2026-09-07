"""Persist V2 scheduler lease, ledger, and desired qB controls.

Revision ID: 20260821_10
Revises: 20260821_09
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_10"
down_revision: str | Sequence[str] | None = "20260821_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "managed_torrents",
        sa.Column("desired_active", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "managed_torrents",
        sa.Column("desired_priority", sa.Integer(), nullable=True),
    )
    op.add_column(
        "managed_torrents",
        sa.Column("desired_download_limit", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "managed_torrents",
        sa.Column("schedule_generation", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_managed_torrents_schedule_values",
        "managed_torrents",
        "desired_download_limit >= 0 AND schedule_generation >= 0",
    )
    op.create_check_constraint(
        "ck_managed_torrents_schedule_state",
        "managed_torrents",
        "(desired_active AND desired_priority IS NOT NULL AND desired_priority >= 0) "
        "OR (NOT desired_active AND desired_priority IS NULL)",
    )
    op.create_index(
        "uq_managed_torrents_schedule_priority",
        "managed_torrents",
        ["schedule_generation", "desired_priority"],
        unique=True,
        postgresql_where=sa.text("desired_active"),
    )
    op.alter_column("managed_torrents", "desired_active", server_default=None)
    op.alter_column("managed_torrents", "desired_download_limit", server_default=None)
    op.alter_column("managed_torrents", "schedule_generation", server_default=None)

    op.create_table(
        "scheduler_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("desired_generation", sa.BigInteger(), nullable=False),
        sa.Column("applied_generation", sa.BigInteger(), nullable=False),
        sa.Column("rounds", sa.BigInteger(), nullable=False),
        sa.Column("cursor_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_scheduler_state_singleton"),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_scheduler_state_lease",
        ),
        sa.CheckConstraint(
            "desired_generation >= 0 AND applied_generation >= 0 "
            "AND applied_generation <= desired_generation",
            name="ck_scheduler_state_generations",
        ),
        sa.CheckConstraint("rounds >= 0", name="ck_scheduler_state_rounds"),
        sa.ForeignKeyConstraint(["cursor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "scheduler_deficits",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("scheduler_state_id", sa.Integer(), nullable=False),
        sa.Column("credit", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scheduler_state_id = 1", name="ck_scheduler_deficits_singleton"),
        sa.CheckConstraint("credit >= 0", name="ck_scheduler_deficits_credit"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scheduler_state_id"], ["scheduler_state.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("scheduler_deficits")
    op.drop_table("scheduler_state")
    op.drop_index("uq_managed_torrents_schedule_priority", table_name="managed_torrents")
    op.drop_constraint("ck_managed_torrents_schedule_state", "managed_torrents", type_="check")
    op.drop_constraint("ck_managed_torrents_schedule_values", "managed_torrents", type_="check")
    op.drop_column("managed_torrents", "schedule_generation")
    op.drop_column("managed_torrents", "desired_download_limit")
    op.drop_column("managed_torrents", "desired_priority")
    op.drop_column("managed_torrents", "desired_active")
