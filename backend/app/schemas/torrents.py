from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TorrentUploadResponse(BaseModel):
    id: str
    name: str
    total_size: int


class UserTorrentResponse(BaseModel):
    id: str
    name: str
    size_bytes: int
    progress: float
    state: Literal["adding", "pending", "downloading", "stalled", "completed", "error"]
    downloaded_bytes: int
    download_speed_bytes: int
    eta_seconds: int | None
    error: str | None
    created_at: datetime


class UserTorrentListingResponse(BaseModel):
    torrents: list[UserTorrentResponse]
