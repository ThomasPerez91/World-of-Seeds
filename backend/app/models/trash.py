from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.auth import User


class TrashEntry(Base):
    __tablename__ = "trash_entries"
    __table_args__ = (
        CheckConstraint("length(original_path) BETWEEN 1 AND 4096", name="ck_trash_path_length"),
        CheckConstraint("length(name) BETWEEN 1 AND 255", name="ck_trash_name_length"),
        CheckConstraint("kind IN ('file', 'directory')", name="ck_trash_kind"),
        Index("ix_trash_entries_user_deleted", "user_id", "deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    device: Mapped[int] = mapped_column(BigInteger, nullable=False)
    inode: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates="trash_entries")
