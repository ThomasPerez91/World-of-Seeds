from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import make_url

from app.core.config import Settings


def test_database_url_handles_reserved_password_characters() -> None:
    settings = Settings(database_url=None, postgres_password=SecretStr("p@ss:/#word"))

    url = make_url(settings.sqlalchemy_database_url)

    assert url.password == "p@ss:/#word"
    assert url.drivername == "postgresql+asyncpg"


def test_cookie_is_not_secure_for_the_initial_ssh_tunnel() -> None:
    settings = Settings()

    assert settings.cookie_secure is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_ttl_hours", 0),
        ("auth_attempt_window_minutes", 0),
        ("auth_lock_minutes", -1),
        ("auth_max_attempts", 0),
        ("postgres_port", 65_536),
        ("allowed_hosts", []),
        ("allowed_hosts", [""]),
        ("session_cookie_name", "invalid cookie"),
        ("data_root", "relative/data"),
        ("qbittorrent_password", ""),
        ("newgreedy_url", "http://user:password@newgreedy:8080"),
        ("qbittorrent_url", "http://qbittorrent:8080/api/v2"),
        ("integration_auth_failure_cache_seconds", 59),
        ("redis_url", "http://redis:6379/0"),
        ("redis_url", "redis://redis:6379/cache"),
        ("redis_url", "redis://redis:6379/0?unsafe=true"),
        ("redis_namespace", "Invalid Namespace"),
        ("redis_cache_ttl_seconds", 0),
        ("redis_signal_queue_max_length", 0),
    ],
)
def test_critical_runtime_settings_reject_unsafe_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


def test_production_data_root_is_fixed_to_the_container_mount() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", data_root=Path("/tmp/data"))

    with pytest.raises(ValidationError):
        Settings(environment="production", qbittorrent_data_root=Path("/seedbox"))


def test_redis_url_credentials_are_hidden_from_settings_representation() -> None:
    settings = Settings(redis_url=SecretStr("rediss://user:password@redis:6379/0"))

    assert "password" not in repr(settings)
