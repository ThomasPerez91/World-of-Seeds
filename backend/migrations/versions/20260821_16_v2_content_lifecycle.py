"""Add managed content lifecycle state.

Revision ID: 20260821_16
Revises: 20260821_15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_16"
down_revision: str | None = "20260821_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATES = (
    "PENDING",
    "ADDING",
    "DOWNLOADING",
    "PAUSED",
    "RETRY_WAIT",
    "ERROR",
    "READY",
    "PURGE_PENDING",
    "PURGED",
)
_NEW_STATES = (*_OLD_STATES[:-1], "PURGING", _OLD_STATES[-1])


def _state_constraint(states: tuple[str, ...]) -> sa.CheckConstraint:
    values = ", ".join(f"'{state}'" for state in states)
    return sa.CheckConstraint(f"state IN ({values})", name="managed_torrent_state")


def upgrade() -> None:
    op.drop_constraint("managed_torrent_state", "managed_torrents", type_="check")
    op.create_check_constraint(
        "managed_torrent_state",
        "managed_torrents",
        _state_constraint(_NEW_STATES).sqltext,
    )
    op.add_column(
        "managed_torrents",
        sa.Column("purge_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "managed_torrents",
        sa.Column("lifecycle_generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_managed_torrents_lifecycle",
        "managed_torrents",
        "lifecycle_generation >= 0 AND "
        "((state IN ('PURGE_PENDING', 'PURGING') AND purge_after IS NOT NULL) "
        "OR (state NOT IN ('PURGE_PENDING', 'PURGING') AND purge_after IS NULL))",
    )
    op.alter_column("managed_torrents", "lifecycle_generation", server_default=None)


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE managed_torrents SET state = 'PURGE_PENDING' "
            "WHERE state = 'PURGING'"
        )
    )
    op.drop_constraint("ck_managed_torrents_lifecycle", "managed_torrents", type_="check")
    op.drop_column("managed_torrents", "lifecycle_generation")
    op.drop_column("managed_torrents", "purge_after")
    op.drop_constraint("managed_torrent_state", "managed_torrents", type_="check")
    op.create_check_constraint(
        "managed_torrent_state",
        "managed_torrents",
        _state_constraint(_OLD_STATES).sqltext,
    )
