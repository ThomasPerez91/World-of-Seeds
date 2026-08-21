import copy
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def _validator() -> tuple[type[RuntimeError], Callable[[dict[str, Any]], None]]:
    repository = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(str(repository / "scripts/validate_compose_v2.py"))
    return namespace["ComposePolicyError"], namespace["validate_config"]


def _valid_config() -> dict[str, Any]:
    healthy = {"test": ["CMD", "true"]}
    return {
        "name": "world-of-seeds-v2",
        "services": {
            "api": {
                "depends_on": {
                    "postgres": {"condition": "service_healthy"},
                    "redis": {"condition": "service_healthy"},
                },
                "healthcheck": healthy,
                "environment": {"WOS_REDIS_URL": "redis://redis:6379/0"},
                "networks": {"backend": None, "edge": None},
                "ports": [{"host_ip": "127.0.0.1", "target": 8000}],
            },
            "worker": {
                "command": ["python", "-m", "app.worker"],
                "depends_on": {
                    "postgres": {"condition": "service_healthy"},
                    "redis": {"condition": "service_healthy"},
                },
                "environment": {"WOS_REDIS_URL": "redis://redis:6379/0"},
                "networks": {"backend": None},
            },
            "postgres": {
                "healthcheck": healthy,
                "image": "postgres:17.11-alpine3.24",
                "networks": {"backend": None},
            },
            "redis": {
                "healthcheck": healthy,
                "image": "redis:8.2.9-alpine3.22",
                "networks": {"backend": None},
            },
        },
        "networks": {"backend": {"internal": True}, "edge": {}},
        "volumes": {"postgres_v2_data": {}, "redis_v2_data": {}},
    }


def test_v2_compose_policy_accepts_the_isolated_foundation() -> None:
    _, validate_config = _validator()

    validate_config(_valid_config())


@pytest.mark.parametrize(
    "break_policy",
    [
        lambda config: config["services"]["postgres"].update({"ports": [{"target": 5432}]}),
        lambda config: config["services"]["redis"].update(
            {"networks": {"backend": None, "edge": None}}
        ),
        lambda config: config["services"]["redis"].update({"image": "redis:latest"}),
        lambda config: config["services"]["api"].pop("healthcheck"),
        lambda config: config["services"]["api"]["environment"].update(
            {"WOS_REDIS_URL": "redis://outside:6379/0"}
        ),
        lambda config: config["networks"]["backend"].update({"internal": False}),
        lambda config: config["services"]["api"].update(
            {"volumes": ["/var/run/docker.sock:/var/run/docker.sock"]}
        ),
        lambda config: config["services"]["worker"].update(
            {"networks": {"backend": None, "edge": None}}
        ),
        lambda config: config["services"]["worker"].update({"ports": [{"target": 9000}]}),
        lambda config: config["services"]["worker"].update(
            {"command": ["uvicorn", "app.main:app"]}
        ),
    ],
)
def test_v2_compose_policy_rejects_an_unsafe_configuration(
    break_policy: Callable[[dict[str, Any]], object],
) -> None:
    error, validate_config = _validator()
    config = copy.deepcopy(_valid_config())
    break_policy(config)

    with pytest.raises(error):
        validate_config(config)
