"""Version V2 torrent manifests.

Revision ID: 20260821_13
Revises: 20260821_12
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_13"
down_revision: str | None = "20260821_12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "managed_torrents",
        sa.Column("manifest_version", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.add_column(
        "managed_torrents",
        sa.Column("manifest_checksum", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "managed_torrents",
        sa.Column("manifest_file_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "managed_torrents",
        sa.Column("manifest_total_size", sa.BigInteger(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_managed_torrents_manifest_values",
        "managed_torrents",
        "manifest_version >= 0 AND manifest_file_count >= 0 AND manifest_total_size >= 0",
    )
    op.create_check_constraint(
        "ck_managed_torrents_manifest_state",
        "managed_torrents",
        "(manifest_version = 0 AND manifest_checksum IS NULL "
        "AND manifest_file_count = 0 AND manifest_total_size = 0) "
        "OR (manifest_version >= 1 AND length(manifest_checksum) = 64)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_managed_torrents_manifest_state",
        "managed_torrents",
        type_="check",
    )
    op.drop_constraint(
        "ck_managed_torrents_manifest_values",
        "managed_torrents",
        type_="check",
    )
    op.drop_column("managed_torrents", "manifest_total_size")
    op.drop_column("managed_torrents", "manifest_file_count")
    op.drop_column("managed_torrents", "manifest_checksum")
    op.drop_column("managed_torrents", "manifest_version")
