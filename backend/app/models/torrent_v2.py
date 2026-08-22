from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.auth import User


class ManagedTorrentState(StrEnum):
    PENDING = "PENDING"
    ADDING = "ADDING"
    DOWNLOADING = "DOWNLOADING"
    PAUSED = "PAUSED"
    RETRY_WAIT = "RETRY_WAIT"
    ERROR = "ERROR"
    READY = "READY"
    PURGE_PENDING = "PURGE_PENDING"
    PURGED = "PURGED"


class TorrentRequestState(StrEnum):
    REQUESTED = "REQUESTED"
    ACTIVE = "ACTIVE"
    READY = "READY"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TorrentJobState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StoragePressureState(StrEnum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class TrackerActivityType(StrEnum):
    ANNOUNCE = "ANNOUNCE"
    SCRAPE = "SCRAPE"
    PROXY_HEALTH = "PROXY_HEALTH"
    TRACKER_STATUS = "TRACKER_STATUS"


class TrackerActivityOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class TrackerDiagnosticCode(StrEnum):
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    TRACKER_REJECTED = "TRACKER_REJECTED"
    RATE_LIMITED = "RATE_LIMITED"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


ACTIVE_REQUEST_PREDICATE = text("state IN ('REQUESTED', 'ACTIVE', 'READY')")


class StorageLedger(Base):
    __tablename__ = "storage_ledger"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_storage_ledger_singleton"),
        CheckConstraint(
            "managed_bytes >= 0 AND disk_total_bytes >= 0 AND disk_free_bytes >= 0 "
            "AND disk_free_bytes <= disk_total_bytes",
            name="ck_storage_ledger_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    managed_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    disk_total_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    disk_free_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    pressure: Mapped[StoragePressureState] = mapped_column(
        Enum(
            StoragePressureState,
            name="storage_pressure_state",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=StoragePressureState.NORMAL,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, nullable=False)


class UserStorageUsage(Base):
    __tablename__ = "user_storage_usage"
    __table_args__ = (
        CheckConstraint("logical_bytes >= 0", name="ck_user_storage_usage_non_negative"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    logical_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates="storage_usage")


class ManagedTorrent(Base):
    __tablename__ = "managed_torrents"
    __table_args__ = (
        CheckConstraint(
            "length(info_hash) = 40 AND info_hash = lower(info_hash)",
            name="ck_managed_torrents_info_hash_canonical",
        ),
        CheckConstraint("total_size >= 0", name="ck_managed_torrents_total_size"),
        CheckConstraint(
            "progress >= 0 AND progress <= 1",
            name="ck_managed_torrents_progress",
        ),
        CheckConstraint(
            "desired_download_limit >= 0 AND schedule_generation >= 0",
            name="ck_managed_torrents_schedule_values",
        ),
        CheckConstraint(
            "manifest_version >= 0 AND manifest_file_count >= 0 AND manifest_total_size >= 0",
            name="ck_managed_torrents_manifest_values",
        ),
        CheckConstraint(
            "(manifest_version = 0 AND manifest_checksum IS NULL "
            "AND manifest_file_count = 0 AND manifest_total_size = 0) "
            "OR (manifest_version >= 1 AND length(manifest_checksum) = 64)",
            name="ck_managed_torrents_manifest_state",
        ),
        CheckConstraint(
            "(desired_active AND desired_priority IS NOT NULL AND desired_priority >= 0) "
            "OR (NOT desired_active AND desired_priority IS NULL)",
            name="ck_managed_torrents_schedule_state",
        ),
        Index(
            "uq_managed_torrents_schedule_priority",
            "schedule_generation",
            "desired_priority",
            unique=True,
            postgresql_where=text("desired_active"),
            sqlite_where=text("desired_active = 1"),
        ),
        Index("ix_managed_torrents_tracker_account", "tracker_account_ref"),
        Index("ix_managed_torrents_qb_account", "qbittorrent_account_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    info_hash: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    storage_key: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), unique=True, default=uuid.uuid4, nullable=False
    )
    name: Mapped[str] = mapped_column(String(4096), nullable=False)
    total_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[ManagedTorrentState] = mapped_column(
        Enum(
            ManagedTorrentState,
            name="managed_torrent_state",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=ManagedTorrentState.PENDING,
        nullable=False,
    )
    qb_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tracker_account_ref: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    qbittorrent_account_ref: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    retry_at: Mapped[datetime | None] = mapped_column(nullable=True)
    manifest_version: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    manifest_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest_file_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manifest_total_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    desired_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    desired_priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    desired_download_limit: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    schedule_generation: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, nullable=False)

    requests: Mapped[list[TorrentRequest]] = relationship(
        back_populates="managed_torrent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    files: Mapped[list[TorrentFile]] = relationship(
        back_populates="managed_torrent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    jobs: Mapped[list[TorrentJob]] = relationship(
        back_populates="managed_torrent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tracker_activities: Mapped[list[TrackerActivity]] = relationship(
        back_populates="managed_torrent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    download_leases: Mapped[list[DownloadLease]] = relationship(
        back_populates="managed_torrent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SchedulerState(Base):
    __tablename__ = "scheduler_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_scheduler_state_singleton"),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_scheduler_state_lease",
        ),
        CheckConstraint(
            "desired_generation >= 0 AND applied_generation >= 0 "
            "AND applied_generation <= desired_generation",
            name="ck_scheduler_state_generations",
        ),
        CheckConstraint("rounds >= 0", name="ck_scheduler_state_rounds"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    desired_generation: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    applied_generation: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    rounds: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    cursor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, nullable=False)


class SchedulerDeficit(Base):
    __tablename__ = "scheduler_deficits"
    __table_args__ = (
        CheckConstraint("scheduler_state_id = 1", name="ck_scheduler_deficits_singleton"),
        CheckConstraint("credit >= 0", name="ck_scheduler_deficits_credit"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    scheduler_state_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scheduler_state.id", ondelete="CASCADE"),
        default=1,
        nullable=False,
    )
    credit: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, nullable=False)


class TorrentRequest(Base):
    __tablename__ = "torrent_requests"
    __table_args__ = (
        Index("ix_torrent_requests_user_created", "user_id", "created_at"),
        Index("ix_torrent_requests_torrent_state", "managed_torrent_id", "state"),
        Index(
            "uq_torrent_requests_active_owner",
            "user_id",
            "managed_torrent_id",
            unique=True,
            postgresql_where=ACTIVE_REQUEST_PREDICATE,
            sqlite_where=ACTIVE_REQUEST_PREDICATE,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    managed_torrent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("managed_torrents.id", ondelete="CASCADE"),
        nullable=False,
    )
    state: Mapped[TorrentRequestState] = mapped_column(
        Enum(
            TorrentRequestState,
            name="torrent_request_state",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=TorrentRequestState.REQUESTED,
        nullable=False,
    )
    ready_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates="torrent_requests")
    managed_torrent: Mapped[ManagedTorrent] = relationship(back_populates="requests")
    jobs: Mapped[list[TorrentJob]] = relationship(
        back_populates="torrent_request",
        passive_deletes=True,
    )


class TorrentFile(Base):
    __tablename__ = "torrent_files"
    __table_args__ = (
        CheckConstraint("file_index >= 0", name="ck_torrent_files_file_index"),
        CheckConstraint("size >= 0", name="ck_torrent_files_size"),
        CheckConstraint(
            "length(relative_path) > 0 "
            "AND substr(relative_path, 1, 1) <> '/' "
            "AND relative_path <> '..' "
            "AND relative_path NOT LIKE '../%' "
            "AND relative_path NOT LIKE '%/../%'",
            name="ck_torrent_files_relative_path",
        ),
        UniqueConstraint(
            "managed_torrent_id",
            "file_index",
            name="uq_torrent_files_torrent_index",
        ),
        UniqueConstraint(
            "managed_torrent_id",
            "relative_path",
            name="uq_torrent_files_torrent_path",
        ),
        Index("ix_torrent_files_torrent", "managed_torrent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    managed_torrent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("managed_torrents.id", ondelete="CASCADE"),
        nullable=False,
    )
    file_index: Mapped[int] = mapped_column(Integer, nullable=False)
    relative_path: Mapped[str] = mapped_column(String(4096), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    managed_torrent: Mapped[ManagedTorrent] = relationship(back_populates="files")


class DownloadLease(Base):
    __tablename__ = "download_leases"
    __table_args__ = (
        Index("ix_download_leases_user_expiry", "user_id", "expires_at"),
        Index("ix_download_leases_torrent_expiry", "managed_torrent_id", "expires_at"),
        Index("ix_download_leases_request", "torrent_request_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    managed_torrent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("managed_torrents.id", ondelete="CASCADE"),
        nullable=False,
    )
    torrent_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("torrent_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    torrent_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("torrent_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    renewed_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)

    managed_torrent: Mapped[ManagedTorrent] = relationship(back_populates="download_leases")


class TrackerActivity(Base):
    __tablename__ = "tracker_activities"
    __table_args__ = (
        CheckConstraint(
            "(outcome = 'SUCCESS' AND diagnostic_code IS NULL) "
            "OR (outcome IN ('DEGRADED', 'FAILED') AND diagnostic_code IS NOT NULL)",
            name="ck_tracker_activities_diagnostic",
        ),
        UniqueConstraint("event_key", name="uq_tracker_activities_event_key"),
        Index(
            "ix_tracker_activities_torrent_occurred",
            "managed_torrent_id",
            "occurred_at",
        ),
        Index(
            "ix_tracker_activities_account_occurred",
            "tracker_account_ref",
            "occurred_at",
        ),
        Index("ix_tracker_activities_outcome_occurred", "outcome", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_key: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    managed_torrent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("managed_torrents.id", ondelete="CASCADE"),
        nullable=False,
    )
    tracker_account_ref: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[TrackerActivityType] = mapped_column(
        Enum(
            TrackerActivityType,
            name="tracker_activity_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    outcome: Mapped[TrackerActivityOutcome] = mapped_column(
        Enum(
            TrackerActivityOutcome,
            name="tracker_activity_outcome",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    diagnostic_code: Mapped[TrackerDiagnosticCode | None] = mapped_column(
        Enum(
            TrackerDiagnosticCode,
            name="tracker_diagnostic_code",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)

    managed_torrent: Mapped[ManagedTorrent] = relationship(back_populates="tracker_activities")


class TorrentJob(Base):
    __tablename__ = "torrent_jobs"
    __table_args__ = (
        CheckConstraint("length(job_type) BETWEEN 1 AND 64", name="ck_torrent_jobs_type"),
        CheckConstraint("length(idempotency_key) BETWEEN 1 AND 128", name="ck_torrent_jobs_key"),
        CheckConstraint("attempt_count >= 0", name="ck_torrent_jobs_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_torrent_jobs_max_attempts"),
        CheckConstraint(
            "state <> 'QUEUED' OR attempt_count < max_attempts",
            name="ck_torrent_jobs_queued_attempts",
        ),
        CheckConstraint(
            "(state = 'RUNNING' AND claimed_by IS NOT NULL "
            "AND claim_expires_at IS NOT NULL AND timeout_at IS NOT NULL) "
            "OR (state <> 'RUNNING' AND claimed_by IS NULL "
            "AND claim_expires_at IS NULL AND timeout_at IS NULL)",
            name="ck_torrent_jobs_claim",
        ),
        CheckConstraint(
            "(state IN ('COMPLETED', 'FAILED', 'CANCELLED') AND finished_at IS NOT NULL) "
            "OR (state IN ('QUEUED', 'RUNNING') AND finished_at IS NULL)",
            name="ck_torrent_jobs_finished",
        ),
        Index("ix_torrent_jobs_claimable", "state", "available_at", "created_at"),
        Index("ix_torrent_jobs_torrent_created", "managed_torrent_id", "created_at"),
        Index("ix_torrent_jobs_request", "torrent_request_id"),
        Index(
            "uq_torrent_jobs_active_sync",
            "managed_torrent_id",
            "job_type",
            unique=True,
            postgresql_where=text("job_type = 'SYNC_TORRENT' AND state IN ('QUEUED', 'RUNNING')"),
            sqlite_where=text("job_type = 'SYNC_TORRENT' AND state IN ('QUEUED', 'RUNNING')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    managed_torrent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("managed_torrents.id", ondelete="CASCADE"),
        nullable=False,
    )
    torrent_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("torrent_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    state: Mapped[TorrentJobState] = mapped_column(
        Enum(
            TorrentJobState,
            name="torrent_job_state",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=TorrentJobState.QUEUED,
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    available_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    timeout_at: Mapped[datetime | None] = mapped_column(nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, nullable=False)

    managed_torrent: Mapped[ManagedTorrent] = relationship(back_populates="jobs")
    torrent_request: Mapped[TorrentRequest | None] = relationship(back_populates="jobs")
