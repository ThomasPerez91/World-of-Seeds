from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

CSRF_COOKIE_NAME = "wos_csrf"
AllowedHost = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=253)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "World of Seeds"
    environment: Literal["development", "test", "production"] = "development"
    allowed_hosts: list[AllowedHost] = Field(
        default_factory=lambda: ["127.0.0.1", "localhost", "test"],
        min_length=1,
    )
    cookie_secure: bool = False
    session_cookie_name: str = Field(
        default="wos_session",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    session_ttl_hours: int = Field(default=12, ge=1, le=720)
    auth_attempt_window_minutes: int = Field(default=15, ge=1, le=1440)
    auth_lock_minutes: int = Field(default=15, ge=1, le=1440)
    auth_max_attempts: int = Field(default=5, ge=1, le=100)
    database_url: str | None = Field(default=None, repr=False)
    postgres_host: str = Field(default="localhost", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="world_of_seeds", min_length=1)
    postgres_user: str = Field(default="world_of_seeds", min_length=1)
    postgres_password: SecretStr = Field(default=SecretStr("world_of_seeds"), repr=False)
    data_root: Path = Path("/data")
    static_root: Path = Path("/app/static")
    newgreedy_url: AnyHttpUrl | None = None
    qbittorrent_url: AnyHttpUrl | None = None
    qbittorrent_username: str | None = Field(default=None, min_length=1, max_length=128)
    qbittorrent_password: SecretStr | None = Field(default=None, repr=False)
    integration_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    integration_read_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    integration_health_cache_seconds: float = Field(default=10.0, ge=1, le=300)

    @field_validator("data_root", "static_root")
    @classmethod
    def require_absolute_container_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Container paths must be absolute")
        return value

    @field_validator("qbittorrent_password")
    @classmethod
    def reject_empty_qbittorrent_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and value.get_secret_value() == "":
            raise ValueError("qBittorrent password must not be empty")
        return value

    @field_validator("newgreedy_url", "qbittorrent_url")
    @classmethod
    def require_origin_only_integration_url(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        if value is None:
            return value
        if value.username is not None or value.password is not None:
            raise ValueError("Integration credentials must not be embedded in URLs")
        if value.path not in ("", "/") or value.query is not None or value.fragment is not None:
            raise ValueError("Integration URLs must contain only an origin")
        return value

    @model_validator(mode="after")
    def require_production_data_mount(self) -> Self:
        if self.environment == "production" and self.data_root != Path("/data"):
            raise ValueError("Production data root must be /data")
        return self

    @property
    def expose_api_docs(self) -> bool:
        return self.environment != "production"

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url is not None:
            return self.database_url

        url = URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )
        return url.render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
