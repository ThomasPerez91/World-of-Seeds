"""Add the durable V2 scheduler backlog cursor.

Revision ID: 20260827_18
Revises: 20260827_17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_18"
down_revision: str | None = "20260827_17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduler_state", sa.Column("scan_cursor_created_at", sa.DateTime(), nullable=True)
    )
    op.add_column("scheduler_state", sa.Column("scan_cursor_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        "ck_scheduler_state_scan_cursor",
        "scheduler_state",
        "(scan_cursor_created_at IS NULL AND scan_cursor_id IS NULL) "
        "OR (scan_cursor_created_at IS NOT NULL AND scan_cursor_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_scheduler_state_scan_cursor", "scheduler_state", type_="check")
    op.drop_column("scheduler_state", "scan_cursor_id")
    op.drop_column("scheduler_state", "scan_cursor_created_at")
