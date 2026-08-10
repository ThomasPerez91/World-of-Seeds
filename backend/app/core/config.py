from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "World of Seeds"
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    database_url: str | None = Field(default=None, repr=False)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "world_of_seeds"
    postgres_user: str = "world_of_seeds"
    postgres_password: SecretStr = Field(default=SecretStr("world_of_seeds"), repr=False)
    data_root: Path = Path("/data")
    static_root: Path = Path("/app/static")

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
