import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TrashFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


class TrashEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_path: str
    name: str
    kind: Literal["directory", "file"]
    size: int | None
    deleted_at: datetime

    @field_validator("deleted_at")
    @classmethod
    def normalize_deleted_at_to_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class TrashListingResponse(BaseModel):
    entries: list[TrashEntryResponse]
    truncated: bool


class RestoredTrashEntryResponse(BaseModel):
    path: str
    name: str
    kind: Literal["directory", "file"]
