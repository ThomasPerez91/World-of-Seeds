"""Persist the user interface theme.

Revision ID: 20260908_23
Revises: 20260831_22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_23"
down_revision: str | None = "20260831_22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("preferred_theme", sa.String(length=6), server_default="system", nullable=False),
    )
    op.create_check_constraint(
        "ck_users_preferred_theme",
        "users",
        "preferred_theme IN ('light', 'dark', 'system')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_preferred_theme", "users", type_="check")
    op.drop_column("users", "preferred_theme")
