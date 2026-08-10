from typing import Literal

from pydantic import BaseModel

from app import __version__


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str = "world-of-seeds"
    version: str = __version__
