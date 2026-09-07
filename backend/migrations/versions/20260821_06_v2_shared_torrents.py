"""Add the V2 shared torrent ownership schema.

Revision ID: 20260821_06
Revises: 20260820_05
Create Date: 2026-08-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_06"
down_revision: str | Sequence[str] | None = "20260820_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

active_request_predicate = sa.text("state IN ('REQUESTED', 'ACTIVE', 'READY')")


def upgrade() -> None:
    op.create_table(
        "managed_torrents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("info_hash", sa.String(length=40), nullable=False),
        sa.Column("storage_key", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=4096), nullable=False),
        sa.Column("total_size", sa.BigInteger(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "PENDING",
                "ADDING",
                "DOWNLOADING",
                "PAUSED",
                "RETRY_WAIT",
                "ERROR",
                "READY",
                "PURGE_PENDING",
                "PURGED",
                name="managed_torrent_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("qb_state", sa.String(length=64), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(info_hash) = 40 AND info_hash = lower(info_hash)",
            name="ck_managed_torrents_info_hash_canonical",
        ),
        sa.CheckConstraint("total_size >= 0", name="ck_managed_torrents_total_size"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("info_hash"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_table(
        "torrent_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("managed_torrent_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "REQUESTED",
                "ACTIVE",
                "READY",
                "CANCELLED",
                "EXPIRED",
                name="torrent_request_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["managed_torrent_id"], ["managed_torrents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_torrent_requests_torrent_state",
        "torrent_requests",
        ["managed_torrent_id", "state"],
    )
    op.create_index(
        "ix_torrent_requests_user_created",
        "torrent_requests",
        ["user_id", "created_at"],
    )
    op.create_index(
        "uq_torrent_requests_active_owner",
        "torrent_requests",
        ["user_id", "managed_torrent_id"],
        unique=True,
        postgresql_where=active_request_predicate,
        sqlite_where=active_request_predicate,
    )
    op.create_table(
        "torrent_files",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("managed_torrent_id", sa.Uuid(), nullable=False),
        sa.Column("file_index", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=4096), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("file_index >= 0", name="ck_torrent_files_file_index"),
        sa.CheckConstraint(
            "length(relative_path) > 0 "
            "AND substr(relative_path, 1, 1) <> '/' "
            "AND relative_path <> '..' "
            "AND relative_path NOT LIKE '../%' "
            "AND relative_path NOT LIKE '%/../%'",
            name="ck_torrent_files_relative_path",
        ),
        sa.CheckConstraint("size >= 0", name="ck_torrent_files_size"),
        sa.ForeignKeyConstraint(
            ["managed_torrent_id"], ["managed_torrents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "managed_torrent_id",
            "file_index",
            name="uq_torrent_files_torrent_index",
        ),
        sa.UniqueConstraint(
            "managed_torrent_id",
            "relative_path",
            name="uq_torrent_files_torrent_path",
        ),
    )
    op.create_index("ix_torrent_files_torrent", "torrent_files", ["managed_torrent_id"])


def downgrade() -> None:
    op.drop_index("ix_torrent_files_torrent", table_name="torrent_files")
    op.drop_table("torrent_files")
    op.drop_index("uq_torrent_requests_active_owner", table_name="torrent_requests")
    op.drop_index("ix_torrent_requests_user_created", table_name="torrent_requests")
    op.drop_index("ix_torrent_requests_torrent_state", table_name="torrent_requests")
    op.drop_table("torrent_requests")
    op.drop_table("managed_torrents")
