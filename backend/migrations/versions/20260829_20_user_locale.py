"""Persist the user interface locale.

Revision ID: 20260829_20
Revises: 20260828_19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_20"
down_revision: str | None = "20260828_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("preferred_locale", sa.String(length=2), server_default="fr", nullable=False),
    )
    op.create_check_constraint(
        "ck_users_preferred_locale",
        "users",
        "preferred_locale IN ('fr', 'en')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_preferred_locale", "users", type_="check")
    op.drop_column("users", "preferred_locale")
