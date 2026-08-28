from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import make_url

from app.core.config import Settings

TEST_PRODUCTION_SECRET = "x" * 32


def test_database_url_handles_reserved_password_characters() -> None:
    settings = Settings(database_url=None, postgres_password=SecretStr("p@ss:/#word"))

    url = make_url(settings.sqlalchemy_database_url)

    assert url.password == "p@ss:/#word"
    assert url.drivername == "postgresql+asyncpg"


def test_cookie_is_not_secure_for_the_initial_ssh_tunnel() -> None:
    settings = Settings()

    assert settings.cookie_secure is False


def test_v1_production_profile_keeps_its_existing_runtime_contract() -> None:
    settings = Settings(environment="production", runtime_profile="v1", cookie_secure=False)

    assert settings.runtime_profile == "v1"


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


def _valid_production_settings() -> dict[str, object]:
    return {
        "environment": "production",
        "runtime_profile": "v2",
        "database_url": None,
        "cookie_secure": True,
        "allowed_hosts": ["world-of-seeds.example", "127.0.0.1"],
        "postgres_host": "postgres",
        "postgres_db": "world_of_seeds_v2",
        "postgres_user": "world_of_seeds_v2",
        "postgres_password": SecretStr(TEST_PRODUCTION_SECRET),
        "redis_url": SecretStr("redis://redis:6379/0"),
    }


def test_production_runtime_requires_secure_non_demo_configuration() -> None:
    settings = Settings.model_validate(_valid_production_settings())

    assert settings.api_process_count == 1
    for change in (
        {"cookie_secure": False},
        {"allowed_hosts": ["*"]},
        {"allowed_hosts": ["localhost"]},
        {"postgres_host": "localhost"},
        {"postgres_password": SecretStr("replace-with-password")},
        {"redis_url": None},
        {"static_root": Path("/tmp/static")},
    ):
        with pytest.raises(ValidationError):
            Settings.model_validate({**_valid_production_settings(), **change})


def test_redis_url_credentials_are_hidden_from_settings_representation() -> None:
    settings = Settings(redis_url=SecretStr("rediss://user:password@redis:6379/0"))

    assert "password" not in repr(settings)
