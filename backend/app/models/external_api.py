from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.auth import User


class ExternalApiClient(Base):
    __tablename__ = "external_api_clients"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 128", name="ck_external_api_clients_name"),
        CheckConstraint("length(key_hash) = 64", name="ck_external_api_clients_key_hash"),
        Index("ix_external_api_clients_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, nullable=False)


class ExternalApiIdempotency(Base):
    __tablename__ = "external_api_idempotency"
    __table_args__ = (
        UniqueConstraint("client_id", "key_hash", name="uq_external_api_idempotency_client_key"),
        CheckConstraint("length(key_hash) = 64", name="ck_external_api_idempotency_key_hash"),
        CheckConstraint(
            "length(request_hash) = 64", name="ck_external_api_idempotency_request_hash"
        ),
        Index("ix_external_api_idempotency_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("external_api_clients.id", ondelete="CASCADE"),
        nullable=False,
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)

    user: Mapped[User] = relationship()


class ExternalApiAudit(Base):
    __tablename__ = "external_api_audits"
    __table_args__ = (
        CheckConstraint("length(action) BETWEEN 1 AND 64", name="ck_external_api_audits_action"),
        CheckConstraint(
            "length(request_id) BETWEEN 1 AND 64", name="ck_external_api_audits_request_id"
        ),
        Index("ix_external_api_audits_created", "created_at"),
        Index("ix_external_api_audits_client", "client_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("external_api_clients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)


class UserProvisioningAudit(Base):
    __tablename__ = "user_provisioning_audits"
    __table_args__ = (
        CheckConstraint(
            "source IN ('admin', 'external_api', 'cli')",
            name="ck_user_provisioning_audits_source",
        ),
        Index("ix_user_provisioning_audits_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    external_client_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("external_api_clients.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
