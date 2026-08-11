from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class FileEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    path: str
    kind: Literal["directory", "file", "symlink", "other"]
    size: int | None
    modified_at: datetime
    media_type: str | None
    blocked: bool


class StorageUsageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    total: int
    used: int
    available: int


class BreadcrumbResponse(BaseModel):
    label: str
    path: str


class DirectoryListingResponse(BaseModel):
    path: str
    breadcrumbs: list[BreadcrumbResponse]
    entries: list[FileEntryResponse]
    storage: StorageUsageResponse
    truncated: bool
