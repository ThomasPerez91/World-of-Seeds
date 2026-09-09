from pydantic import BaseModel, Field


class AdminStorageResponse(BaseModel):
    total: int = Field(ge=0)
    used: int = Field(ge=0)
    available: int = Field(ge=0)
    active_users: int = Field(ge=0)
    suspended_users: int = Field(ge=0)
