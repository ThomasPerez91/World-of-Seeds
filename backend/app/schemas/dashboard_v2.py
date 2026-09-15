from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class NetworkThroughputSampleResponse(BaseModel):
    timestamp: datetime
    value_bytes_per_second: float = Field(ge=0)


class NetworkThroughputDirectionResponse(BaseModel):
    current_bytes_per_second: float = Field(ge=0)
    samples: list[NetworkThroughputSampleResponse]


class NetworkThroughputResponse(BaseModel):
    status: Literal["ok", "no_data", "unavailable"]
    period: Literal["realtime"] = "realtime"
    sample_interval_seconds: int = Field(default=15, ge=1)
    download: NetworkThroughputDirectionResponse | None = None
    upload: NetworkThroughputDirectionResponse | None = None
