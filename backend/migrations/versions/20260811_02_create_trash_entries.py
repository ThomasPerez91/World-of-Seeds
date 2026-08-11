"""Create trash entry metadata.

Revision ID: 20260811_02
Revises: 20260810_01
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_02"
down_revision: str | Sequence[str] | None = "20260810_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trash_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("original_path", sa.Text(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("device", sa.BigInteger(), nullable=False),
        sa.Column("inode", sa.BigInteger(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(original_path) BETWEEN 1 AND 4096",
            name="ck_trash_path_length",
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 255", name="ck_trash_name_length"),
        sa.CheckConstraint("kind IN ('file', 'directory')", name="ck_trash_kind"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trash_entries_user_deleted",
        "trash_entries",
        ["user_id", "deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_trash_entries_user_deleted", table_name="trash_entries")
    op.drop_table("trash_entries")
