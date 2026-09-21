"""Track the last successful user login.

Revision ID: 20260921_33
Revises: 20260921_32
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_33"
down_revision: str | None = "20260921_32"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE users AS account "
            "SET last_login_at = ("
            "SELECT MAX(session.created_at) FROM user_sessions AS session "
            "WHERE session.user_id = account.id"
            ") "
            "WHERE EXISTS ("
            "SELECT 1 FROM user_sessions AS session "
            "WHERE session.user_id = account.id"
            ")"
        )
    )
    op.create_index("ix_users_last_login_at", "users", ["last_login_at"])


def downgrade() -> None:
    op.drop_index("ix_users_last_login_at", table_name="users")
    op.drop_column("users", "last_login_at")
