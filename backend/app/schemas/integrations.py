from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ConfigValue = bool | int | float | str
ConfigIdentifier = Annotated[str, Field(pattern=r"^[a-z_]+\.[a-z_]+$", max_length=128)]


class NewGreedyConfigFieldResponse(BaseModel):
    id: str
    key: str
    label: str
    description: str
    input_type: Literal["boolean", "integer", "number", "text", "select"]
    value: ConfigValue
    editable: bool
    requires_restart: bool = True
    minimum: float | None
    maximum: float | None
    options: list[str]


class NewGreedyConfigSectionResponse(BaseModel):
    id: str
    label: str
    fields: list[NewGreedyConfigFieldResponse]


class NewGreedyConfigResponse(BaseModel):
    sections: list[NewGreedyConfigSectionResponse]
    restart_required: bool = False


class NewGreedyConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict[ConfigIdentifier, ConfigValue] = Field(min_length=1, max_length=42)


class NewGreedyOverviewResponse(BaseModel):
    torrents: int = Field(ge=0)
    downloading: int = Field(ge=0)
    seeding: int = Field(ge=0)
    stalled: int = Field(ge=0)
    target_reached: int = Field(ge=0)
    total_downloaded_bytes: int = Field(ge=0)
    total_reported_uploaded_bytes: int = Field(ge=0)
    total_fake_uploaded_bytes: int = Field(ge=0)


class NewGreedyStatsResetResponse(BaseModel):
    purged: int = Field(ge=0)
    remaining: int = Field(ge=0)


class NewGreedyTorrentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(pattern=r"^[0-9a-f]{8,40}$")
    mode: Literal["down", "seed"]
    downloaded_bytes: int = Field(ge=0)
    reported_uploaded_bytes: int = Field(ge=0)
    fake_uploaded_bytes: int = Field(ge=0)
    ratio: float | None = Field(default=None, ge=0)
    announce_count: int = Field(ge=0)
    stalled: bool
    target_reached: bool
    last_announce_at: datetime | None


class NewGreedyTorrentListingResponse(BaseModel):
    torrents: list[NewGreedyTorrentResponse]


class QBittorrentTorrentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(pattern=r"^[0-9a-f]{40}([0-9a-f]{24})?$")
    name: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=64)
    progress: float = Field(ge=0, le=1)
    size_bytes: int = Field(ge=0)
    downloaded_bytes: int = Field(ge=0)
    uploaded_bytes: int = Field(ge=0)
    download_speed_bytes: int = Field(ge=0)
    upload_speed_bytes: int = Field(ge=0)
    ratio: float = Field(ge=0)
    eta_seconds: int | None = Field(default=None, ge=0)
    category: str | None = Field(default=None, max_length=256)
    tracker_host: str | None = Field(default=None, max_length=253)


class QBittorrentTorrentListingResponse(BaseModel):
    torrents: list[QBittorrentTorrentResponse]
    truncated: bool


SECTION_LABELS = {
    "proxy": "Proxy",
    "spoofing": "Simulation",
    "anti_detection": "Discrétion",
    "ssl": "TLS",
    "stats": "Statistiques",
    "web": "Interface interne",
    "advanced": "Avancé",
}
