"""Persist dynamic qBittorrent download-concurrency state.

Revision ID: 20260921_32
Revises: 20260920_31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_32"
down_revision: str | None = "20260920_31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduler_state",
        sa.Column("dynamic_current_active", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column("scheduler_state", sa.Column("dynamic_last_evaluated_at", sa.DateTime()))
    op.add_column("scheduler_state", sa.Column("dynamic_cooldown_until", sa.DateTime()))
    op.add_column("scheduler_state", sa.Column("dynamic_sampled_at", sa.DateTime()))
    op.add_column(
        "scheduler_state",
        sa.Column("dynamic_sample_baseline", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "scheduler_state",
        sa.Column(
            "dynamic_observed_bytes_per_second",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "scheduler_state",
        sa.Column("dynamic_active_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "scheduler_state",
        sa.Column("dynamic_waiting_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("scheduler_state", sa.Column("dynamic_next_evaluation_at", sa.DateTime()))
    op.add_column(
        "scheduler_state",
        sa.Column(
            "dynamic_last_decision",
            sa.String(length=64),
            nullable=False,
            server_default="dynamic_idle_reset",
        ),
    )
    op.create_check_constraint(
        "ck_scheduler_state_dynamic_values",
        "scheduler_state",
        "dynamic_current_active >= 0 AND dynamic_observed_bytes_per_second >= 0 "
        "AND dynamic_active_count >= 0 AND dynamic_waiting_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_scheduler_state_dynamic_values", "scheduler_state", type_="check")
    for column in (
        "dynamic_last_decision",
        "dynamic_next_evaluation_at",
        "dynamic_waiting_count",
        "dynamic_active_count",
        "dynamic_observed_bytes_per_second",
        "dynamic_sample_baseline",
        "dynamic_sampled_at",
        "dynamic_cooldown_until",
        "dynamic_last_evaluated_at",
        "dynamic_current_active",
    ):
        op.drop_column("scheduler_state", column)
