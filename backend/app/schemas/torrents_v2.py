import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TorrentRequestV2Response(BaseModel):
    id: uuid.UUID
    name: str
    total_size: int
    state: Literal["requested", "active", "ready", "cancelled", "expired", "error"]
    progress: float = Field(ge=0, le=1)
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class TorrentRequestV2CreateResponse(TorrentRequestV2Response):
    created: bool
    storage_pressure: Literal["normal", "warning", "critical"]


class TorrentRequestV2ListingResponse(BaseModel):
    items: list[TorrentRequestV2Response]
    offset: int
    limit: int
    total: int


class TorrentDownloadFileResponse(BaseModel):
    id: uuid.UUID
    file_index: int = Field(ge=0)
    relative_path: str
    size: int = Field(ge=0)


class TorrentDownloadManifestResponse(BaseModel):
    snapshot_id: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    manifest_version: int = Field(ge=1)
    file_count: int = Field(ge=1)
    total_size: int = Field(ge=0)
    archive_available: bool
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    items: list[TorrentDownloadFileResponse]
