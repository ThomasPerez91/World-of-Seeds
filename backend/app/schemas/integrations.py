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


SECTION_LABELS = {
    "proxy": "Proxy",
    "spoofing": "Simulation",
    "anti_detection": "Discrétion",
    "ssl": "TLS",
    "stats": "Statistiques",
    "web": "Interface interne",
    "advanced": "Avancé",
}
