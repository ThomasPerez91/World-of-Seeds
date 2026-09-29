"""Keep ZIP concurrency independent from file streams.

Revision ID: 20260929_35
Revises: 20260925_34
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_35"
down_revision: str | None = "20260925_34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "download_leases",
        sa.Column("kind", sa.String(length=8), nullable=False, server_default="file"),
    )
    op.create_check_constraint(
        "ck_download_leases_kind",
        "download_leases",
        "kind IN ('file', 'archive')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_download_leases_kind", "download_leases", type_="check")
    op.drop_column("download_leases", "kind")
