"""Add administrable C411 account slots.

Revision ID: 20260911_27
Revises: 20260911_26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_27"
down_revision: str | None = "20260911_26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _keys() -> tuple[str, ...]:
    return tuple(
        key
        for slot in range(1, 17)
        for key in (
            f"WOS_C411_ACCOUNT_{slot:02d}_NUMBER",
            f"WOS_C411_ACCOUNT_{slot:02d}_PASSKEY",
        )
    )


def upgrade() -> None:
    op.drop_constraint("ck_database_options_typed_value", "database_options", type_="check")
    op.drop_constraint("ck_database_options_type", "database_options", type_="check")
    op.alter_column(
        "database_options",
        "string_value",
        existing_type=sa.String(length=128),
        type_=sa.String(length=512),
        existing_nullable=True,
    )
    op.create_check_constraint(
        "ck_database_options_type",
        "database_options",
        "value_type IN ('boolean', 'integer', 'select', 'text', 'secret')",
    )
    op.create_check_constraint(
        "ck_database_options_typed_value",
        "database_options",
        "(value_type = 'boolean' AND boolean_value IS NOT NULL "
        "AND integer_value IS NULL AND string_value IS NULL) "
        "OR (value_type = 'integer' AND boolean_value IS NULL "
        "AND integer_value IS NOT NULL AND string_value IS NULL) "
        "OR (value_type IN ('select', 'text', 'secret') AND boolean_value IS NULL "
        "AND integer_value IS NULL AND string_value IS NOT NULL)",
    )

    for slot in range(1, 17):
        for suffix, value_type in (("NUMBER", "text"), ("PASSKEY", "secret")):
            key = f"WOS_C411_ACCOUNT_{slot:02d}_{suffix}"
            op.execute(
                sa.text(
                    "INSERT INTO database_options "
                    "(key, value_type, boolean_value, integer_value, string_value, "
                    "minimum_value, maximum_value, choices, editable, restart_required, "
                    "version, updated_by_user_id, created_at, updated_at) VALUES "
                    f"('{key}', '{value_type}', NULL, NULL, '', NULL, NULL, "
                    "CAST('[]' AS JSON), true, false, 1, NULL, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )


def downgrade() -> None:
    keys = ", ".join(f"'{key}'" for key in _keys())
    op.execute(sa.text(f"DELETE FROM database_option_audits WHERE option_key IN ({keys})"))
    op.execute(sa.text(f"DELETE FROM database_options WHERE key IN ({keys})"))
    op.drop_constraint("ck_database_options_typed_value", "database_options", type_="check")
    op.drop_constraint("ck_database_options_type", "database_options", type_="check")
    op.alter_column(
        "database_options",
        "string_value",
        existing_type=sa.String(length=512),
        type_=sa.String(length=128),
        existing_nullable=True,
    )
    op.create_check_constraint(
        "ck_database_options_type",
        "database_options",
        "value_type IN ('boolean', 'integer', 'select')",
    )
    op.create_check_constraint(
        "ck_database_options_typed_value",
        "database_options",
        "(value_type = 'boolean' AND boolean_value IS NOT NULL "
        "AND integer_value IS NULL AND string_value IS NULL) "
        "OR (value_type = 'integer' AND boolean_value IS NULL "
        "AND integer_value IS NOT NULL AND string_value IS NULL) "
        "OR (value_type = 'select' AND boolean_value IS NULL "
        "AND integer_value IS NULL AND string_value IS NOT NULL)",
    )
