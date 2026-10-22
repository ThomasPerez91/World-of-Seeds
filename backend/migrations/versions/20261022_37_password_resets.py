"""Single-use password recovery grants.

Revision ID: 20261022_37
Revises: 20261015_36
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_37"
down_revision: str | None = "20261015_36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "password_resets",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "client_id",
            sa.Uuid(),
            sa.ForeignKey("external_api_clients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("client_key_hash", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("password_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(token_hash) = 64", name="ck_password_resets_token_hash"),
    )


def downgrade() -> None:
    op.drop_table("password_resets")
