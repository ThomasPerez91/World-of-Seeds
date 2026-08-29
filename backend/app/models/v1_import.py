from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utc_now


class V1ImportRunStatus(StrEnum):
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"


class V1ImportRun(Base):
    __tablename__ = "v1_import_runs"
    __table_args__ = (
        CheckConstraint("length(source_fingerprint) = 64", name="ck_v1_import_fingerprint"),
        CheckConstraint(
            "source_rows >= 0 AND created_torrents >= 0 AND created_requests >= 0",
            name="ck_v1_import_counts",
        ),
        UniqueConstraint("source_fingerprint", name="uq_v1_import_source_fingerprint"),
        Index("ix_v1_import_runs_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_snapshot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    backup_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[V1ImportRunStatus] = mapped_column(
        Enum(
            V1ImportRunStatus,
            name="v1_import_run_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=V1ImportRunStatus.APPLIED,
        nullable=False,
    )
    source_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    created_torrents: Mapped[int] = mapped_column(Integer, nullable=False)
    created_requests: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    rolled_back_at: Mapped[datetime | None] = mapped_column(nullable=True)


class V1ImportItem(Base):
    __tablename__ = "v1_import_items"
    __table_args__ = (
        UniqueConstraint("run_id", "source_record_id", name="uq_v1_import_run_source_record"),
        Index("ix_v1_import_items_run", "run_id"),
        Index("ix_v1_import_items_request", "target_request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("v1_import_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_record_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_managed_torrent_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    target_request_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    managed_torrent_created: Mapped[bool] = mapped_column(Boolean, nullable=False)
