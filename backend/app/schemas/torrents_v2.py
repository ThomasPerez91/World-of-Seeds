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
