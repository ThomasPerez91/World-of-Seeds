from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class AdminStorageResponse(BaseModel):
    total: int
    used: int
    available: int
    active_users: int
    suspended_users: int
    trash_entries: int
    known_trash_bytes: int


class AdminTrashEntryResponse(BaseModel):
    id: UUID
    user_id: UUID
    username: str
    original_path: str
    name: str
    kind: Literal["directory", "file"]
    size: int | None
    deleted_at: datetime


class AdminTrashListingResponse(BaseModel):
    entries: list[AdminTrashEntryResponse]
    truncated: bool


class AdminTrashPurgeResponse(BaseModel):
    purged: int
    remaining: int
