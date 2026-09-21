from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Username = Annotated[str, StringConstraints(min_length=3, max_length=32)]
Password = Annotated[str, StringConstraints(min_length=12, max_length=256)]
Locale = Literal["fr", "en"]
Theme = Literal["light", "dark", "system"]


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
    preferred_locale: Locale
    preferred_theme: Theme


class AuthResponse(BaseModel):
    user: UserResponse


class AdminUserResponse(UserResponse):
    created_at: datetime
    last_login_at: datetime | None


class ChangeCredentialsRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    username: Username
    new_password: Password


class ChangeUsernameRequest(BaseModel):
    username: Username


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: Password


class ChangeLocaleRequest(BaseModel):
    preferred_locale: Locale


class ChangeThemeRequest(BaseModel):
    preferred_theme: Theme


class UserStatusRequest(BaseModel):
    is_active: bool


class GeneratedCredentialsResponse(BaseModel):
    user: AdminUserResponse
    initial_password: str
    auth_seed: str = Field(min_length=25, max_length=25)


class AuthSeedResponse(BaseModel):
    auth_seed: str = Field(min_length=25, max_length=25)


class UserQuotaResponse(BaseModel):
    used: int = Field(ge=0)
    maximum: int = Field(gt=0)
    reached: bool
