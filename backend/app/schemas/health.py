from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app import __version__


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str = "world-of-seeds"
    version: str = __version__


class PublicSystemHealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checked_at: datetime


class ServiceHealthDetail(BaseModel):
    status: Literal["healthy", "unavailable", "unconfigured"]
    latency_ms: int | None
    version: str | None
    error_code: str | None


class AdminSystemHealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checked_at: datetime
    newgreedy: ServiceHealthDetail
    qbittorrent: ServiceHealthDetail
