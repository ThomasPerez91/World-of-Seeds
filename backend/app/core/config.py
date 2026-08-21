from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

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
    redis_url: SecretStr | None = Field(default=None, repr=False)
    redis_namespace: str = Field(
        default="wos:v2",
        min_length=3,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9:_-]+$",
    )
    redis_connect_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    redis_socket_timeout_seconds: float = Field(default=1.0, gt=0, le=10)
    redis_cache_ttl_seconds: int = Field(default=60, ge=1, le=86_400)
    redis_cache_stale_seconds: int = Field(default=300, ge=0, le=86_400)
    redis_signal_queue_max_length: int = Field(default=1_000, ge=1, le=100_000)
    data_root: Path = Path("/data")
    static_root: Path = Path("/app/static")
    newgreedy_url: AnyHttpUrl | None = None
    qbittorrent_url: AnyHttpUrl | None = None
    qbittorrent_username: str | None = Field(default=None, min_length=1, max_length=128)
    qbittorrent_password: SecretStr | None = Field(default=None, repr=False)
    qbittorrent_data_root: Path = Path("/data")
    c411_passkey: SecretStr | None = Field(default=None, repr=False)
    integration_accounts_json: SecretStr | None = Field(default=None, repr=False)
    c411_tracker_hosts: list[AllowedHost] = Field(
        default_factory=lambda: ["c411.org", "tk.c411.tw"],
        min_length=1,
    )
    integration_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    integration_read_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    integration_health_cache_seconds: float = Field(default=10.0, ge=1, le=300)
    integration_auth_failure_cache_seconds: float = Field(default=300.0, ge=60, le=3600)
    newgreedy_config_max_bytes: int = Field(default=128 * 1024, ge=1024, le=1024 * 1024)

    @field_validator("data_root", "static_root", "qbittorrent_data_root")
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

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        parsed = urlsplit(value.get_secret_value())
        if (
            parsed.scheme not in {"redis", "rediss"}
            or parsed.hostname is None
            or parsed.query
            or parsed.fragment
            or (parsed.path not in {"", "/"} and not parsed.path.removeprefix("/").isdigit())
        ):
            raise ValueError("Redis URL must be a redis/rediss origin with an optional DB number")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Redis URL port is invalid") from exc
        return value

    @field_validator("c411_passkey")
    @classmethod
    def validate_c411_passkey(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        secret = value.get_secret_value()
        if (
            not 8 <= len(secret) <= 256
            or not secret.isascii()
            or any(character in "/?#" for character in secret)
        ):
            raise ValueError("C411 passkey is invalid")
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
        if self.environment == "production" and (
            self.data_root != Path("/data") or self.qbittorrent_data_root != Path("/data")
        ):
            raise ValueError("Production WOS and qBittorrent data roots must be /data")
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
