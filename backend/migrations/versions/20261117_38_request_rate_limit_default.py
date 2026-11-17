"""Raise the per-account request budget from its unused default before wiring it.

WOS_REQUEST_RATE_LIMIT_PER_MINUTE was registered with a default of 60 but read by no
consumer and hidden from administration, so 60 is still the bootstrap value everywhere. Its
new consumer applies it to every authenticated business request, which needs 300. Only a row
still holding the untouched 60 is raised; any other value was chosen and is kept. The change
is versioned and audited like an administrator edit.

Revision ID: 20261117_38
Revises: 20261022_37
"""

import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261117_38"
down_revision: str | None = "20261022_37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = "WOS_REQUEST_RATE_LIMIT_PER_MINUTE"
PREVIOUS_DEFAULT = 60
NEW_DEFAULT = 300


def _replace(old: int, new: int) -> None:
    connection = op.get_bind()
    row = connection.execute(
        sa.text("SELECT version, integer_value FROM database_options WHERE key = :key FOR UPDATE"),
        {"key": KEY},
    ).first()
    # A database whose options were never bootstrapped receives the registry default later.
    if row is None or row.integer_value != old:
        return
    version = row.version + 1
    connection.execute(
        sa.text(
            "UPDATE database_options SET integer_value = :new, version = :version, "
            "updated_by_user_id = NULL, updated_at = now() WHERE key = :key"
        ),
        {"new": new, "version": version, "key": KEY},
    )
    connection.execute(
        sa.text(
            "INSERT INTO database_option_audits "
            "(id, option_key, version, old_value, new_value, actor_user_id, change_source, "
            "changed_at) "
            "VALUES (:id, :key, :version, CAST(:old AS JSON), CAST(:new AS JSON), NULL, "
            "'migration', now())"
        ),
        {
            "id": uuid.uuid4(),
            "key": KEY,
            "version": version,
            "old": json.dumps(old),
            "new": json.dumps(new),
        },
    )


def upgrade() -> None:
    _replace(PREVIOUS_DEFAULT, NEW_DEFAULT)


def downgrade() -> None:
    _replace(NEW_DEFAULT, PREVIOUS_DEFAULT)
