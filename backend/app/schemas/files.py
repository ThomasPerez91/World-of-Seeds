from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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


class RenameFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=4096)
    basename: str = Field(min_length=1, max_length=255)


class MoveFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(max_length=4096)
    destination_directory: str = Field(max_length=4096)


class CreateDirectoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent: str = Field(max_length=4096)
    name: str = Field(min_length=1, max_length=255)


class FileMutationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    path: str
    name: str
    kind: Literal["directory", "file"]
