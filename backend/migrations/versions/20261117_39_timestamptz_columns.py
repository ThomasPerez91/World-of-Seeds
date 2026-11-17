"""Store the last fourteen naive timestamp columns with their time zone.

Earlier revisions created these columns as ``timestamp without time zone`` while the models
declare ``DateTime(timezone=True)``. The application always wrote UTC into them (it binds UTC
values stripped of their offset), so ``AT TIME ZONE 'UTC'`` keeps every instant unchanged
whatever the session time zone. Each table is rewritten once; its indexes are rebuilt with it.

Revision ID: 20261117_39
Revises: 20261117_38
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261117_39"
down_revision: str | None = "20261117_38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS: dict[str, tuple[str, ...]] = {
    "download_leases": ("expires_at", "created_at", "renewed_at"),
    "integration_service_health": ("checked_at", "valid_until", "updated_at"),
    "managed_torrents": ("last_progress_at", "scheduler_retry_at"),
    "qbittorrent_inventory_snapshots": ("checked_at",),
    "scheduler_state": (
        "scan_cursor_created_at",
        "dynamic_last_evaluated_at",
        "dynamic_cooldown_until",
        "dynamic_sampled_at",
        "dynamic_next_evaluation_at",
    ),
}


def _convert(target_type: str) -> None:
    # The deploy helper stops api, worker and scheduler first; never wait long for a lock.
    op.execute("SET LOCAL lock_timeout = '10s'")
    for table, columns in COLUMNS.items():
        clauses = ", ".join(
            f'ALTER COLUMN "{column}" TYPE {target_type} USING "{column}" AT TIME ZONE \'UTC\''
            for column in columns
        )
        op.execute(f'ALTER TABLE "{table}" {clauses}')


def upgrade() -> None:
    _convert("timestamp with time zone")


def downgrade() -> None:
    _convert("timestamp without time zone")
