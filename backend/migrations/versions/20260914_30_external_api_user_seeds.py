"""Add account quota, user auth seeds and external API clients.

Revision ID: 20260914_30
Revises: 20260912_29
"""

import secrets
import string
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_30"
down_revision: str | None = "20260912_29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALPHABET = string.ascii_letters + string.digits


def _seed() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(25))


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_seed", sa.String(length=25), nullable=True))
    connection = op.get_bind()
    used: set[str] = set()
    for user_id in connection.execute(sa.text("SELECT id FROM users ORDER BY id")).scalars():
        candidate = _seed()
        while candidate in used:
            candidate = _seed()
        used.add(candidate)
        connection.execute(
            sa.text("UPDATE users SET auth_seed = :seed WHERE id = :user_id"),
            {"seed": candidate, "user_id": user_id},
        )
    null_count = connection.execute(
        sa.text("SELECT count(*) FROM users WHERE auth_seed IS NULL")
    ).scalar_one()
    if null_count:
        raise RuntimeError("user auth seed backfill is incomplete")
    op.create_unique_constraint("uq_users_auth_seed", "users", ["auth_seed"])
    op.create_check_constraint("ck_users_auth_seed_length", "users", "length(auth_seed) = 25")
    op.alter_column("users", "auth_seed", existing_type=sa.String(length=25), nullable=False)

    op.create_table(
        "external_api_clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("key_prefix", sa.String(length=24), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 128", name="ck_external_api_clients_name"),
        sa.CheckConstraint("length(key_hash) = 64", name="ck_external_api_clients_key_hash"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index("ix_external_api_clients_active", "external_api_clients", ["is_active"])
    op.create_table(
        "external_api_idempotency",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("created_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(key_hash) = 64", name="ck_external_api_idempotency_key_hash"),
        sa.CheckConstraint(
            "length(request_hash) = 64", name="ck_external_api_idempotency_request_hash"
        ),
        sa.ForeignKeyConstraint(["client_id"], ["external_api_clients.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "key_hash", name="uq_external_api_idempotency_client_key"),
    )
    op.create_index(
        "ix_external_api_idempotency_created", "external_api_idempotency", ["created_at"]
    )
    op.create_table(
        "external_api_audits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_fingerprint", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(action) BETWEEN 1 AND 64", name="ck_external_api_audits_action"),
        sa.CheckConstraint(
            "length(request_id) BETWEEN 1 AND 64", name="ck_external_api_audits_request_id"
        ),
        sa.ForeignKeyConstraint(["client_id"], ["external_api_clients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_external_api_audits_created", "external_api_audits", ["created_at"])
    op.create_index("ix_external_api_audits_client", "external_api_audits", ["client_id"])
    op.create_table(
        "user_provisioning_audits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("external_client_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('admin', 'external_api', 'cli')", name="ck_user_provisioning_audits_source"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["external_client_id"], ["external_api_clients.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_provisioning_audits_created", "user_provisioning_audits", ["created_at"]
    )

    connection.execute(
        sa.text(
            "INSERT INTO database_options (key, value_type, boolean_value, integer_value, "
            "string_value, minimum_value, maximum_value, choices, editable, restart_required, "
            "version, updated_by_user_id, created_at, updated_at) VALUES "
            "('WOS_MAX_USER_ACCOUNTS', 'integer', NULL, 100, NULL, 1, 100000, "
            "CAST('[]' AS JSON), true, false, 1, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO database_option_audits (id, option_key, version, old_value, new_value, "
            "actor_user_id, change_source, changed_at) VALUES "
            "(:id, 'WOS_MAX_USER_ACCOUNTS', 1, NULL, CAST('100' AS JSON), NULL, "
            "'bootstrap', CURRENT_TIMESTAMP)"
        ),
        {"id": uuid.uuid4()},
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM database_option_audits WHERE option_key = 'WOS_MAX_USER_ACCOUNTS'")
    )
    connection.execute(sa.text("DELETE FROM database_options WHERE key = 'WOS_MAX_USER_ACCOUNTS'"))
    op.drop_index("ix_user_provisioning_audits_created", table_name="user_provisioning_audits")
    op.drop_table("user_provisioning_audits")
    op.drop_index("ix_external_api_audits_client", table_name="external_api_audits")
    op.drop_index("ix_external_api_audits_created", table_name="external_api_audits")
    op.drop_table("external_api_audits")
    op.drop_index("ix_external_api_idempotency_created", table_name="external_api_idempotency")
    op.drop_table("external_api_idempotency")
    op.drop_index("ix_external_api_clients_active", table_name="external_api_clients")
    op.drop_table("external_api_clients")
    op.drop_constraint("ck_users_auth_seed_length", "users", type_="check")
    op.drop_constraint("uq_users_auth_seed", "users", type_="unique")
    op.drop_column("users", "auth_seed")
