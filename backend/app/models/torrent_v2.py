from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
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


ACTIVE_REQUEST_PREDICATE = text("state IN ('REQUESTED', 'ACTIVE', 'READY')")


class ManagedTorrent(Base):
    __tablename__ = "managed_torrents"
    __table_args__ = (
        CheckConstraint(
            "length(info_hash) = 40 AND info_hash = lower(info_hash)",
            name="ck_managed_torrents_info_hash_canonical",
        ),
        CheckConstraint("total_size >= 0", name="ck_managed_torrents_total_size"),
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
    retry_at: Mapped[datetime | None] = mapped_column(nullable=True)
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
