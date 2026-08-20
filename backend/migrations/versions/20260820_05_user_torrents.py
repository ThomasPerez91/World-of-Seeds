"""Persist the minimal V1 user-to-torrent association.

Revision ID: 20260820_05
Revises: 20260813_04
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_05"
down_revision: str | Sequence[str] | None = "20260813_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_torrents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("info_hash", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=4096), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "info_hash", name="uq_user_torrents_user_hash"),
    )
    op.create_index(
        "ix_user_torrents_user_created",
        "user_torrents",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_torrents_user_created", table_name="user_torrents")
    op.drop_table("user_torrents")
