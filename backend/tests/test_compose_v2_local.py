import copy
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def _validator() -> tuple[type[RuntimeError], Callable[[dict[str, Any]], None]]:
    repository = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(str(repository / "scripts/validate_compose_v2_local.py"))
    return namespace["ComposeLocalPolicyError"], namespace["validate_config"]


def _valid_config() -> dict[str, Any]:
    environment = {"WOS_ENVIRONMENT": "development", "WOS_RUNTIME_PROFILE": "v2"}
    private = {"networks": {"backend": None}}
    wos = {
        **private,
        "environment": environment,
        "user": "10001:10001",
        "volumes": [{"type": "volume", "source": "storage_v2_local", "target": "/data"}],
    }
    qb = {
        **private,
        "image": "qbittorrentofficial/qbittorrent-nox:5.2.3-1",
    }
    return {
        "name": "world-of-seeds-v2-local",
        "services": {
            "api": {
                **wos,
                "networks": {"backend": None, "edge": None},
                "ports": [{"host_ip": "127.0.0.1", "target": 8000}],
                "environment": {
                    **environment,
                    "WOS_API_PROCESS_COUNT": "1",
                    "WOS_ALLOWED_HOSTS": '["127.0.0.1","localhost","api"]',
                },
                "command": ["uvicorn", "app.main:app"],
            },
            "worker": {**wos, "command": ["python", "-m", "app.worker"]},
            "scheduler": {
                **wos,
                "command": ["python", "-m", "app.scheduler_service"],
            },
            "postgres": private,
            "redis": private,
            "newgreedy": {**private, "healthcheck": {"test": ["CMD", "true"]}},
            "qbittorrent-init": qb,
            "qbittorrent": {**qb, "healthcheck": {"test": ["CMD", "true"]}},
        },
        "networks": {"backend": {"internal": True}, "edge": {}},
        "volumes": {
            "postgres_v2_data": {},
            "redis_v2_data": {},
            "storage_v2_local": {},
            "qbittorrent_v2_local_config": {},
        },
    }


def test_local_compose_policy_accepts_complete_private_stack() -> None:
    _, validate = _validator()
    validate(_valid_config())


@pytest.mark.parametrize(
    "break_policy",
    [
        lambda config: config.update({"name": "world-of-seeds-v2"}),
        lambda config: config["services"]["qbittorrent"].update(
            {"ports": [{"host_ip": "127.0.0.1", "target": 8080}]}
        ),
        lambda config: config["services"]["postgres"].update(
            {"networks": {"backend": None, "edge": None}}
        ),
        lambda config: config["services"]["worker"].update({"user": "1000:1000"}),
        lambda config: config["services"]["api"].update(
            {"volumes": [{"type": "bind", "source": "/srv/wos", "target": "/data"}]}
        ),
        lambda config: config["services"]["api"]["environment"].update(
            {"WOS_ALLOWED_HOSTS": '["127.0.0.1","localhost","api","public.example"]'}
        ),
        lambda config: config["services"]["api"]["environment"].update(
            {"WOS_API_PROCESS_COUNT": "2"}
        ),
        lambda config: config["services"]["api"].update(
            {"command": ["uvicorn", "app.main:app", "--workers", "2"]}
        ),
        lambda config: config["services"]["qbittorrent"].update(
            {"image": "qbittorrentofficial/qbittorrent-nox:latest"}
        ),
        lambda config: config["networks"]["backend"].update({"internal": False}),
    ],
)
def test_local_compose_policy_rejects_unsafe_variants(
    break_policy: Callable[[dict[str, Any]], object],
) -> None:
    error, validate = _validator()
    config = copy.deepcopy(_valid_config())
    break_policy(config)
    with pytest.raises(error):
        validate(config)
