"""Add durable V2 download leases.

Revision ID: 20260821_15
Revises: 20260821_14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_15"
down_revision: str | None = "20260821_14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_leases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("managed_torrent_id", sa.Uuid(), nullable=False),
        sa.Column("torrent_request_id", sa.Uuid(), nullable=False),
        sa.Column("torrent_file_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("renewed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["managed_torrent_id"], ["managed_torrents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["torrent_file_id"], ["torrent_files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["torrent_request_id"], ["torrent_requests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_download_leases_user_expiry",
        "download_leases",
        ["user_id", "expires_at"],
    )
    op.create_index(
        "ix_download_leases_torrent_expiry",
        "download_leases",
        ["managed_torrent_id", "expires_at"],
    )
    op.create_index(
        "ix_download_leases_request",
        "download_leases",
        ["torrent_request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_download_leases_request", table_name="download_leases")
    op.drop_index("ix_download_leases_torrent_expiry", table_name="download_leases")
    op.drop_index("ix_download_leases_user_expiry", table_name="download_leases")
    op.drop_table("download_leases")
