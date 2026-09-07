"""Add typed and audited V2 database options.

Revision ID: 20260821_08
Revises: 20260821_07
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_08"
down_revision: str | Sequence[str] | None = "20260821_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "database_options",
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value_type", sa.String(length=16), nullable=False),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        sa.Column("integer_value", sa.BigInteger(), nullable=True),
        sa.Column("string_value", sa.String(length=128), nullable=True),
        sa.Column("minimum_value", sa.BigInteger(), nullable=True),
        sa.Column("maximum_value", sa.BigInteger(), nullable=True),
        sa.Column("choices", sa.JSON(), nullable=False),
        sa.Column("editable", sa.Boolean(), nullable=False),
        sa.Column("restart_required", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(key) BETWEEN 5 AND 128 AND substr(key, 1, 4) = 'WOS_'",
            name="ck_database_options_key",
        ),
        sa.CheckConstraint(
            "value_type IN ('boolean', 'integer', 'select')",
            name="ck_database_options_type",
        ),
        sa.CheckConstraint("version >= 1", name="ck_database_options_version"),
        sa.CheckConstraint(
            "(value_type = 'boolean' AND boolean_value IS NOT NULL "
            "AND integer_value IS NULL AND string_value IS NULL) "
            "OR (value_type = 'integer' AND boolean_value IS NULL "
            "AND integer_value IS NOT NULL AND string_value IS NULL) "
            "OR (value_type = 'select' AND boolean_value IS NULL "
            "AND integer_value IS NULL AND string_value IS NOT NULL)",
            name="ck_database_options_typed_value",
        ),
        sa.CheckConstraint(
            "(value_type = 'integer' AND minimum_value IS NOT NULL "
            "AND maximum_value IS NOT NULL AND minimum_value <= maximum_value) "
            "OR (value_type <> 'integer' AND minimum_value IS NULL AND maximum_value IS NULL)",
            name="ck_database_options_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "database_option_audits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("option_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("old_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("change_source", sa.String(length=32), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_database_option_audits_version"),
        sa.CheckConstraint(
            "length(change_source) BETWEEN 1 AND 32",
            name="ck_database_option_audits_source",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["option_key"],
            ["database_options.key"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "option_key",
            "version",
            name="uq_database_option_audits_version",
        ),
    )
    op.create_index(
        "ix_database_option_audits_actor",
        "database_option_audits",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_database_option_audits_changed",
        "database_option_audits",
        ["changed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_database_option_audits_changed", table_name="database_option_audits")
    op.drop_index("ix_database_option_audits_actor", table_name="database_option_audits")
    op.drop_table("database_option_audits")
    op.drop_table("database_options")
