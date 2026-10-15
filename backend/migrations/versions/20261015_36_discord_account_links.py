"""Bind Discord identities to provisioned accounts.

Revision ID: 20261015_36
Revises: 20260929_35
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_36"
down_revision: str | None = "20260929_35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discord_account_links",
        sa.Column("discord_user_id", sa.String(length=20), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "client_id",
            sa.Uuid(),
            sa.ForeignKey("external_api_clients.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("discord_account_links")
