"""Persist explicit administrator qBittorrent resume overrides.

Revision ID: 20260920_31
Revises: 20260914_30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_31"
down_revision: str | None = "20260914_30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "managed_torrents",
        sa.Column(
            "admin_forced_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column("managed_torrents", "admin_forced_active", server_default=None)


def downgrade() -> None:
    op.drop_column("managed_torrents", "admin_forced_active")
