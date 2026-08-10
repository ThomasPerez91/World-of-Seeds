from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Username = Annotated[str, StringConstraints(min_length=3, max_length=32)]
Password = Annotated[str, StringConstraints(min_length=12, max_length=256)]


class LoginRequest(BaseModel):
    username: Username
    password: str = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    is_admin: bool
    is_active: bool
    must_change_credentials: bool
    expires_at: datetime | None


class AuthResponse(BaseModel):
    user: UserResponse


class ChangeCredentialsRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    username: Username
    new_password: Password


class TemporaryUserRequest(BaseModel):
    expires_in_days: int = Field(default=7, ge=1, le=30)


class TemporaryCredentialsResponse(BaseModel):
    user: UserResponse
    temporary_password: str
