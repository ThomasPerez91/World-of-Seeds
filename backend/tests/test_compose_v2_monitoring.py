import copy
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def _validator() -> tuple[type[RuntimeError], Callable[..., None]]:
    repository = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(str(repository / "scripts/validate_compose_v2_monitoring.py"))
    return namespace["ComposeMonitoringPolicyError"], namespace["validate_config"]


def _valid_config() -> dict[str, Any]:
    monitoring = {"networks": {"monitoring": None}, "profiles": ["monitoring"]}
    bind = {"type": "bind", "source": "/config", "target": "/config", "read_only": True}
    root_bind = {
        "type": "bind",
        "source": "/",
        "target": "/host/root",
        "read_only": True,
        "bind": {"propagation": "rslave"},
    }
    return {
        "name": "world-of-seeds-v2-local",
        "services": {
            "api": {"networks": {"backend": None, "edge": None}},
            "prometheus": {
                **monitoring,
                "image": "prom/prometheus:v3.14.0",
                "networks": {"backend": None, "monitoring": None},
                "command": ["--storage.tsdb.retention.time=15d"],
                "volumes": [bind],
            },
            "grafana": {
                **monitoring,
                "image": "grafana/grafana:13.2.0",
                "networks": {"monitoring": None, "monitoring-edge": None},
                "ports": [{"host_ip": "127.0.0.1", "target": 3000}],
                "environment": {
                    "GF_AUTH_ANONYMOUS_ENABLED": "false",
                    "GF_SECURITY_ADMIN_USER": "admin",
                    "GF_SECURITY_ADMIN_PASSWORD": "not-a-real-secret",
                    "GF_ANALYTICS_REPORTING_ENABLED": "false",
                    "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
                    "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false",
                },
                "volumes": [bind],
            },
            "node-exporter": {
                **monitoring,
                "image": "prom/node-exporter:v1.12.1",
                "volumes": [root_bind],
            },
            "cadvisor": {
                **monitoring,
                "image": "ghcr.io/google/cadvisor:v0.60.5",
                "privileged": True,
                "volumes": [
                    {**bind, "source": "/var/run", "target": "/var/run"},
                ],
            },
        },
        "networks": {
            "backend": {"internal": True},
            "edge": {},
            "monitoring": {"internal": True},
            "monitoring-edge": {},
        },
    }


def test_monitoring_policy_accepts_isolated_pinned_stack() -> None:
    _, validate = _validator()
    validate(_valid_config())


def test_monitoring_policy_accepts_docker_desktop_without_rslave() -> None:
    _, validate = _validator()
    config = _valid_config()
    config["services"]["node-exporter"]["volumes"][0]["bind"]["propagation"] = "rprivate"
    validate(config, platform="docker-desktop")


def test_monitoring_policy_keeps_platform_mount_contracts_distinct() -> None:
    error, validate = _validator()
    linux = _valid_config()
    desktop = copy.deepcopy(linux)
    desktop["services"]["node-exporter"]["volumes"][0]["bind"]["propagation"] = "rprivate"
    with pytest.raises(error, match="requires rslave"):
        validate(desktop, platform="linux")
    with pytest.raises(error, match="requires rprivate"):
        validate(linux, platform="docker-desktop")


@pytest.mark.parametrize(
    "break_policy",
    [
        lambda config: config["services"]["grafana"].update(
            {"ports": [{"host_ip": "0.0.0.0", "target": 3000}]}
        ),
        lambda config: config["networks"]["monitoring"].update({"internal": False}),
        lambda config: config["networks"]["monitoring-edge"].update({"internal": True}),
        lambda config: config["services"]["prometheus"].update({"image": "prom/prometheus:latest"}),
        lambda config: config["services"]["prometheus"].update({"command": []}),
        lambda config: config["services"]["node-exporter"].update(
            {"networks": {"backend": None, "monitoring": None}}
        ),
        lambda config: config["services"]["grafana"].update(
            {"volumes": [{"type": "bind", "source": "/config", "target": "/config"}]}
        ),
        lambda config: config["services"]["node-exporter"].update(
            {
                "volumes": [
                    {
                        "type": "bind",
                        "source": "/var/run/docker.sock",
                        "target": "/var/run/docker.sock",
                        "read_only": True,
                    }
                ]
            }
        ),
    ],
)
def test_monitoring_policy_rejects_unsafe_variants(
    break_policy: Callable[[dict[str, Any]], object],
) -> None:
    error, validate = _validator()
    config = copy.deepcopy(_valid_config())
    break_policy(config)
    with pytest.raises(error):
        validate(config)
