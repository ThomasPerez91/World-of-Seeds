import json
import uuid

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.worker import validate_worker_runtime

TEST_DATABASE_SECRET = "d" * 32
TEST_TRACKER_SECRET = "t" * 32
TEST_QB_SECRET = "q" * 32


def _production(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "runtime_profile": "v2",
        "allowed_hosts": ["seeds.example"],
        "cookie_secure": True,
        "database_url": (f"postgresql+asyncpg://wos:{TEST_DATABASE_SECRET}@postgres:5432/wos"),
        "redis_url": SecretStr("redis://redis:6379/0"),
    }
    values.update(overrides)
    return Settings.model_validate(values)


def _registry(*, qb_password: str = TEST_QB_SECRET) -> SecretStr:
    return SecretStr(
        json.dumps(
            {
                "routes": [
                    {
                        "tracker_account_ref": str(uuid.uuid4()),
                        "qbittorrent_account_ref": str(uuid.uuid4()),
                        "newgreedy_url": "http://newgreedy:8080",
                        "c411_passkey": TEST_TRACKER_SECRET,
                        "qbittorrent_url": "http://qbittorrent:8080",
                        "qbittorrent_username": "worker",
                        "qbittorrent_password": qb_password,
                    }
                ]
            }
        )
    )


def test_production_api_and_worker_accept_complete_secure_runtime() -> None:
    settings = _production(integration_accounts_json=_registry())

    validate_worker_runtime(settings)


def test_production_worker_fails_fast_without_integration_registry() -> None:
    with pytest.raises(RuntimeError, match="^v2_worker_integrations_required$"):
        validate_worker_runtime(_production())


def test_production_worker_rejects_demo_secret_without_disclosing_it() -> None:
    secret = "local-test-qb-secret"
    with pytest.raises(RuntimeError) as caught:
        validate_worker_runtime(
            _production(integration_accounts_json=_registry(qb_password=secret))
        )

    assert str(caught.value) == "v2_worker_integration_secret_invalid"
    assert secret not in str(caught.value)


def test_development_worker_can_run_without_external_integrations() -> None:
    validate_worker_runtime(Settings(environment="development"))


def test_production_api_rejects_multiple_processes_before_startup() -> None:
    with pytest.raises(ValidationError):
        _production(api_process_count=2)


def test_runtime_accepts_the_single_process_value_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WOS_API_PROCESS_COUNT", "1")

    assert Settings().api_process_count == 1
