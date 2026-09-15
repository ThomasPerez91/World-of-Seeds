from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ExternalScope = Literal["users:create", "downloads:read"]


class ExternalHealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    api_version: Literal["1"] = "1"


class ExternalUserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str | None = Field(default=None, min_length=3, max_length=32)


class ExternalUserCreateResponse(BaseModel):
    id: UUID
    username: str
    temporary_password: str | None
    auth_seed: str = Field(min_length=25, max_length=25)
    must_change_credentials: bool
    idempotent_replay: bool = False


class ExternalDownloadResponse(BaseModel):
    id: UUID
    name: str
    state: Literal[
        "waiting", "downloading", "stalled", "cooldown", "ready", "error", "cancelled", "expired"
    ]
    progress_percent: float = Field(ge=0, le=100)
    size_bytes: int = Field(ge=0)
    queue_position: int | None = Field(default=None, ge=1)
    created_at: datetime
    updated_at: datetime
    ready_at: datetime | None
    unsubscribe_at: datetime | None
    eta_seconds: int | None = Field(default=None, ge=0)
    access_remaining_seconds: int | None = Field(default=None, ge=0)


class ExternalDownloadsPage(BaseModel):
    items: list[ExternalDownloadResponse]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class ExternalApiClientCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    scopes: list[ExternalScope] = Field(min_length=1, max_length=2)


class ExternalApiClientResponse(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    is_active: bool
    scopes: list[ExternalScope]
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ExternalApiClientCreatedResponse(ExternalApiClientResponse):
    api_key: str


class ExternalErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ExternalErrorResponse(BaseModel):
    error: ExternalErrorDetail
