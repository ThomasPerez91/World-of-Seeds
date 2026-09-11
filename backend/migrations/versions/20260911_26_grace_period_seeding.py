"""Allow deferred purge deadlines while torrents keep seeding or downloading.

Revision ID: 20260911_26
Revises: 20260910_25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_26"
down_revision: str | None = "20260910_25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_managed_torrents_lifecycle",
        "managed_torrents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_managed_torrents_lifecycle",
        "managed_torrents",
        "lifecycle_generation >= 0 AND "
        "(state NOT IN ('PURGE_PENDING', 'PURGING') OR purge_after IS NOT NULL) AND "
        "(state <> 'PURGED' OR purge_after IS NULL)",
    )


def downgrade() -> None:
    # Restore the former eager-purge representation before restoring its constraint.
    op.execute(
        sa.text(
            "UPDATE managed_torrents "
            "SET state = 'PURGE_PENDING', desired_active = false, "
            "desired_priority = NULL, desired_download_limit = 0, "
            "purge_stop_pending = true "
            "WHERE purge_after IS NOT NULL "
            "AND state NOT IN ('PURGE_PENDING', 'PURGING')"
        )
    )
    op.drop_constraint(
        "ck_managed_torrents_lifecycle",
        "managed_torrents",
        type_="check",
    )
    op.create_check_constraint(
        "ck_managed_torrents_lifecycle",
        "managed_torrents",
        "lifecycle_generation >= 0 AND "
        "((state IN ('PURGE_PENDING', 'PURGING') AND purge_after IS NOT NULL) "
        "OR (state NOT IN ('PURGE_PENDING', 'PURGING') AND purge_after IS NULL))",
    )
