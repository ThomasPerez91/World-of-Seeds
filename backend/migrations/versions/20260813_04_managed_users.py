"""Replace expiring invitations with managed user accounts.

Revision ID: 20260813_04
Revises: 20260813_03
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_04"
down_revision: str | Sequence[str] | None = "20260813_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_column("users", "expires_at")


def downgrade() -> None:
    op.add_column("users", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_column("users", "deleted_at")
