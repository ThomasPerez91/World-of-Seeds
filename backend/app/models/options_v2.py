from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.auth import User
    from app.options.registry import OptionSpec, OptionValue


class DatabaseOption(Base):
    __tablename__ = "database_options"
    __table_args__ = (
        CheckConstraint(
            "length(key) BETWEEN 5 AND 128 AND substr(key, 1, 4) = 'WOS_'",
            name="ck_database_options_key",
        ),
        CheckConstraint(
            "value_type IN ('boolean', 'integer', 'select')",
            name="ck_database_options_type",
        ),
        CheckConstraint("version >= 1", name="ck_database_options_version"),
        CheckConstraint(
            "(value_type = 'boolean' AND boolean_value IS NOT NULL "
            "AND integer_value IS NULL AND string_value IS NULL) "
            "OR (value_type = 'integer' AND boolean_value IS NULL "
            "AND integer_value IS NOT NULL AND string_value IS NULL) "
            "OR (value_type = 'select' AND boolean_value IS NULL "
            "AND integer_value IS NULL AND string_value IS NOT NULL)",
            name="ck_database_options_typed_value",
        ),
        CheckConstraint(
            "(value_type = 'integer' AND minimum_value IS NOT NULL "
            "AND maximum_value IS NOT NULL AND minimum_value <= maximum_value) "
            "OR (value_type <> 'integer' AND minimum_value IS NULL AND maximum_value IS NULL)",
            name="ck_database_options_bounds",
        ),
    )

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    boolean_value: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    integer_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    string_value: Mapped[str | None] = mapped_column(String(128), nullable=True)
    minimum_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    maximum_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    choices: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    restart_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now, nullable=False)

    updated_by: Mapped[User | None] = relationship(foreign_keys=[updated_by_user_id])
    audit_events: Mapped[list[DatabaseOptionAudit]] = relationship(
        back_populates="option",
        passive_deletes=True,
    )

    @property
    def value(self) -> OptionValue:
        if self.value_type == "boolean" and self.boolean_value is not None:
            return self.boolean_value
        if self.value_type == "integer" and self.integer_value is not None:
            return self.integer_value
        if self.value_type == "select" and self.string_value is not None:
            return self.string_value
        raise RuntimeError(f"Database option {self.key} has an inconsistent typed value")

    def set_value(self, spec: OptionSpec, value: OptionValue) -> None:
        self.value_type = spec.input_type
        self.boolean_value = value if type(value) is bool else None
        self.integer_value = value if type(value) is int else None
        self.string_value = value if isinstance(value, str) else None


class DatabaseOptionAudit(Base):
    __tablename__ = "database_option_audits"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_database_option_audits_version"),
        CheckConstraint(
            "length(change_source) BETWEEN 1 AND 32",
            name="ck_database_option_audits_source",
        ),
        UniqueConstraint("option_key", "version", name="uq_database_option_audits_version"),
        Index("ix_database_option_audits_changed", "changed_at"),
        Index("ix_database_option_audits_actor", "actor_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    option_key: Mapped[str] = mapped_column(
        String(128),
        ForeignKey("database_options.key", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    old_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[object] = mapped_column(JSON, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    change_source: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)

    option: Mapped[DatabaseOption] = relationship(back_populates="audit_events")
    actor: Mapped[User | None] = relationship(foreign_keys=[actor_user_id])
