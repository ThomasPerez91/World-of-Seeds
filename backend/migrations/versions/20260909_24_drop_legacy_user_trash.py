"""Drop the legacy per-user trash metadata table.

Revision ID: 20260909_24
Revises: 20260908_23

The downgrade recreates the structural schema only. Metadata deleted by the
upgrade cannot be restored, and this migration never mutates physical content.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_24"
down_revision: str | None = "20260908_23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_trash_entries_user_deleted", table_name="trash_entries")
    op.drop_table("trash_entries")


def downgrade() -> None:
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
