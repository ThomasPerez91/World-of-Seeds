import copy
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def _validator() -> tuple[type[RuntimeError], Callable[[dict[str, Any]], None]]:
    repository = Path(__file__).resolve().parents[2]
    namespace = runpy.run_path(str(repository / "scripts/validate_compose_v2_rise2.py"))
    return namespace["ComposeRise2PolicyError"], namespace["validate_config"]


def _runtime(image: str, networks: set[str]) -> dict[str, Any]:
    return {
        "image": image,
        "networks": {name: None for name in networks},
        "environment": {
            "WOS_ENVIRONMENT": "production",
            "WOS_RUNTIME_PROFILE": "v2",
            "WOS_COOKIE_SECURE": "true",
            "WOS_REDIS_URL": "redis://redis:6379/0",
            "WOS_INTEGRATION_ACCOUNTS_JSON": '{"routes":[]}',
        },
        "read_only": True,
        "cap_drop": ["ALL"],
    }


def _valid_config() -> dict[str, Any]:
    digest = "example.invalid/wos@sha256:" + "1" * 64
    newgreedy_digest = "example.invalid/newgreedy@sha256:" + "2" * 64
    services: dict[str, Any] = {
        "ingress": {
            "image": "caddy:2.10.2-alpine",
            "networks": {"edge": None, "monitoring-edge": None},
            "ports": [{}, {}, {}],
            "cap_drop": ["ALL"],
            "cap_add": ["NET_BIND_SERVICE"],
        },
        "migrate": _runtime(digest, {"backend"}),
        "api": _runtime(digest, {"edge", "backend"}),
        "worker": _runtime(digest, {"backend", "torrent"}),
        "scheduler": _runtime(digest, {"backend", "torrent"}),
        "postgres": {"image": "postgres:17.11-alpine3.24", "networks": {"backend": None}},
        "redis": {"image": "redis:8.2.9-alpine3.22", "networks": {"backend": None}},
        "qbittorrent-init": {
            "image": "qbittorrentofficial/qbittorrent-nox:5.2.3-1",
            "networks": {"torrent": None},
        },
        "qbittorrent": {
            "image": "qbittorrentofficial/qbittorrent-nox:5.2.3-1",
            "networks": {"torrent": None},
        },
        "newgreedy": {
            "image": newgreedy_digest,
            "networks": {"torrent": None},
            "cap_drop": ["ALL"],
            "volumes": [
                {
                    "type": "bind",
                    "source": "/etc/world-of-seeds-v2/config.ini",
                    "target": "/app/config.ini",
                    "read_only": True,
                }
            ],
        },
        "prometheus": {
            "image": "prom/prometheus:v3.14.0",
            "networks": {"backend": None, "monitoring": None},
        },
        "grafana": {
            "image": "grafana/grafana:13.2.0",
            "networks": {"monitoring": None, "monitoring-edge": None},
        },
        "node-exporter": {
            "image": "prom/node-exporter:v1.12.1",
            "networks": {"monitoring": None},
        },
        "cadvisor": {
            "image": "ghcr.io/google/cadvisor:v0.60.5",
            "networks": {"monitoring": None},
            "privileged": True,
        },
    }
    return {
        "name": "world-of-seeds-v2-rise2",
        "services": services,
        "networks": {
            "edge": {},
            "backend": {"internal": True},
            "torrent": {"internal": True},
            "monitoring": {"internal": True},
            "monitoring-edge": {"internal": True},
        },
        "volumes": {
            name: {}
            for name in (
                "postgres_v2_data",
                "redis_v2_data",
                "qbittorrent_v2_config",
                "newgreedy_v2_data",
                "prometheus_v2_data",
                "grafana_v2_data",
                "caddy_v2_data",
                "caddy_v2_config",
            )
        },
    }


def test_rise2_policy_accepts_complete_isolated_stack() -> None:
    _, validate = _validator()
    validate(_valid_config())


@pytest.mark.parametrize(
    "break_policy",
    [
        lambda config: config["services"]["postgres"].update({"ports": [5432]}),
        lambda config: config["networks"]["torrent"].update({"internal": False}),
        lambda config: config["services"]["worker"].update(
            {"networks": {"backend": None, "torrent": None, "edge": None}}
        ),
        lambda config: config["services"]["api"].update({"command": ["uvicorn", "--workers", "2"]}),
        lambda config: config["services"]["newgreedy"].update({"privileged": True}),
        lambda config: config["services"]["cadvisor"].update({"privileged": False}),
        lambda config: config["services"]["prometheus"].update({"privileged": True}),
        lambda config: config["services"]["newgreedy"].update(
            {"volumes": [{"type": "bind", "target": "/app/config.ini"}]}
        ),
        lambda config: config["services"]["api"].update({"image": "example.invalid/wos:latest"}),
        lambda config: config["services"]["api"]["environment"].update(
            {"WOS_INTEGRATION_ACCOUNTS_JSON": ""}
        ),
    ],
)
def test_rise2_policy_rejects_unsafe_or_cross_network_variants(
    break_policy: Callable[[dict[str, Any]], object],
) -> None:
    error, validate = _validator()
    config = copy.deepcopy(_valid_config())
    break_policy(config)
    with pytest.raises(error):
        validate(config)
