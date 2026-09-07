"""Add durable V2 torrent progress.

Revision ID: 20260821_14
Revises: 20260821_13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_14"
down_revision: str | None = "20260821_13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "managed_torrents",
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_managed_torrents_progress",
        "managed_torrents",
        "progress >= 0 AND progress <= 1",
    )
    op.alter_column("managed_torrents", "progress", server_default=None)


def downgrade() -> None:
    op.drop_constraint(
        "ck_managed_torrents_progress",
        "managed_torrents",
        type_="check",
    )
    op.drop_column("managed_torrents", "progress")
