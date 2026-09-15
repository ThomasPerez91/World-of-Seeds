"""Add optional C411 account usernames.

Revision ID: 20260912_29
Revises: 20260911_28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_29"
down_revision: str | None = "20260911_28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _keys() -> tuple[str, ...]:
    return tuple(f"WOS_C411_ACCOUNT_{slot:02d}_USERNAME" for slot in range(1, 17))


def upgrade() -> None:
    for key in _keys():
        op.execute(
            sa.text(
                "INSERT INTO database_options "
                "(key, value_type, boolean_value, integer_value, string_value, "
                "minimum_value, maximum_value, choices, editable, restart_required, "
                "version, updated_by_user_id, created_at, updated_at) VALUES "
                f"('{key}', 'text', NULL, NULL, '', NULL, NULL, "
                "CAST('[]' AS JSON), true, false, 1, NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )


def downgrade() -> None:
    keys = ", ".join(f"'{key}'" for key in _keys())
    op.execute(sa.text(f"DELETE FROM database_option_audits WHERE option_key IN ({keys})"))
    op.execute(sa.text(f"DELETE FROM database_options WHERE key IN ({keys})"))
