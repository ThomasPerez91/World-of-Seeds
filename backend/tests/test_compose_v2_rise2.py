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
            "networks": {"edge": {"ipv4_address": "172.30.0.2"}, "monitoring-edge": None},
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
            "networks": {"torrent": None, "torrent-egress": None},
            "environment": {"UMASK": "077"},
            "entrypoint": ["/bin/sh", "-ec"],
            "command": [
                "/wos-ca/mitmproxy-ca-cert.pem /etc/ssl/certs/ca-certificates.crt "
                "PRIVATE KEY exec /sbin/tini -g -- /entrypoint.sh"
            ],
            "depends_on": {
                "qbittorrent-init": {"condition": "service_completed_successfully"},
                "newgreedy-ca-export": {"condition": "service_completed_successfully"},
            },
            "volumes": [
                {
                    "type": "volume",
                    "source": "world-of-seeds-v2-rise2_qbittorrent_v2_config",
                    "target": "/config",
                },
                {
                    "type": "bind",
                    "source": "/srv/world-of-seeds-v2/data",
                    "target": "/data",
                },
                {
                    "type": "volume",
                    "source": "world-of-seeds-v2-rise2_newgreedy_v2_public_ca",
                    "target": "/wos-ca",
                    "read_only": True,
                },
            ],
        },
        "newgreedy-init": {
            "image": newgreedy_digest,
            "network_mode": "none",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only": True,
            "command": [
                "prepare stats.json torrent_registry.json newgreedy.log purge_pending.json"
            ],
            "volumes": [
                {
                    "type": "bind",
                    "source": "/srv/world-of-seeds-v2/newgreedy-state",
                    "target": "/state",
                    "bind": {"create_host_path": False},
                }
            ],
        },
        "newgreedy": {
            "image": newgreedy_digest,
            "networks": {"torrent": None, "torrent-egress": None},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only": True,
            "group_add": ["10003"],
            "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=32m"],
            "depends_on": {"newgreedy-init": {"condition": "service_completed_successfully"}},
            "healthcheck": {"test": ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/api/health"]},
            "volumes": [
                {
                    "type": "bind",
                    "source": "/etc/world-of-seeds-v2/config.ini",
                    "target": "/app/config.ini",
                    "read_only": True,
                    "bind": {"create_host_path": False},
                },
                *[
                    {
                        "type": "bind",
                        "source": f"/srv/world-of-seeds-v2/newgreedy-state/{name}",
                        "target": f"/app/{name}",
                        "bind": {"create_host_path": False},
                    }
                    for name in (
                        "stats.json",
                        "torrent_registry.json",
                        "newgreedy.log",
                        "purge_pending.json",
                    )
                ],
                {
                    "type": "volume",
                    "source": "world-of-seeds-v2-rise2_newgreedy_v2_ca",
                    "target": "/root/.mitmproxy",
                },
            ],
        },
        "newgreedy-ca-export": {
            "image": newgreedy_digest,
            "network_mode": "none",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only": True,
            "depends_on": {"newgreedy": {"condition": "service_healthy"}},
            "command": ["/private/mitmproxy-ca-cert.pem /public/mitmproxy-ca-cert.pem PRIVATE KEY"],
            "volumes": [
                {
                    "type": "volume",
                    "source": "world-of-seeds-v2-rise2_newgreedy_v2_ca",
                    "target": "/private",
                    "read_only": True,
                },
                {
                    "type": "volume",
                    "source": "world-of-seeds-v2-rise2_newgreedy_v2_public_ca",
                    "target": "/public",
                },
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
    services["api"]["networks"]["edge"] = {"ipv4_address": "172.30.0.3"}
    services["api"]["environment"]["FORWARDED_ALLOW_IPS"] = "172.30.0.2"
    services["worker"]["environment"]["WOS_INTEGRATION_ACCOUNTS_JSON"] = '{"routes":[]}'
    services["scheduler"]["environment"]["WOS_INTEGRATION_ACCOUNTS_JSON"] = '{"routes":[]}'
    return {
        "name": "world-of-seeds-v2-rise2",
        "services": services,
        "networks": {
            "edge": {"ipam": {"config": [{"subnet": "172.30.0.0/24"}]}},
            "backend": {"internal": True},
            "torrent": {"internal": True},
            "torrent-egress": {},
            "monitoring": {"internal": True},
            "monitoring-edge": {"internal": True},
        },
        "volumes": {
            name: {}
            for name in (
                "postgres_v2_data",
                "redis_v2_data",
                "qbittorrent_v2_config",
                "newgreedy_v2_ca",
                "newgreedy_v2_public_ca",
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


def test_newgreedy_smoke_uses_an_isolated_compose_project() -> None:
    repository = Path(__file__).resolve().parents[2]
    script = (repository / "scripts/rise2_v2_newgreedy_smoke.sh").read_text(encoding="utf-8")

    assert 'readonly project_name="world-of-seeds-v2-rise2-smoke-$run_suffix"' in script
    assert 'docker compose --project-name "$project_name"' in script


@pytest.mark.parametrize(
    "break_policy",
    [
        lambda config: config["services"]["postgres"].update({"ports": [5432]}),
        lambda config: config["networks"]["torrent"].update({"internal": False}),
        lambda config: config["networks"]["torrent-egress"].update({"internal": True}),
        lambda config: config["services"]["worker"].update(
            {"networks": {"backend": None, "torrent": None, "edge": None}}
        ),
        lambda config: config["services"]["worker"]["networks"].update({"torrent-egress": None}),
        lambda config: config["services"]["migrate"]["networks"].update({"torrent-egress": None}),
        lambda config: config["services"]["qbittorrent-init"]["networks"].update(
            {"torrent-egress": None}
        ),
        lambda config: config["services"]["newgreedy-init"].update(
            {"networks": {"torrent-egress": None}}
        ),
        lambda config: config["services"]["newgreedy-ca-export"].update(
            {"networks": {"torrent-egress": None}}
        ),
        lambda config: config["services"]["qbittorrent"]["environment"].update({"UMASK": "022"}),
        lambda config: config["services"]["qbittorrent"]["volumes"].append(
            {
                "type": "volume",
                "source": "world-of-seeds-v2-rise2_newgreedy_v2_ca",
                "target": "/leaked-private-ca",
                "read_only": True,
            }
        ),
        lambda config: config["services"]["qbittorrent"]["depends_on"][
            "newgreedy-ca-export"
        ].update({"condition": "service_started"}),
        lambda config: config["services"]["newgreedy-ca-export"]["volumes"][0].update(
            {"read_only": False}
        ),
        lambda config: config["services"]["newgreedy-ca-export"].update(
            {"command": ["cat /private/mitmproxy-ca.pem > /public/ca.pem PRIVATE KEY"]}
        ),
        lambda config: config["services"]["api"].update({"command": ["uvicorn", "--workers", "2"]}),
        lambda config: config["services"]["newgreedy"].update({"privileged": True}),
        lambda config: config["services"]["newgreedy"].update({"user": "10003:10003"}),
        lambda config: config["services"]["newgreedy"].update({"security_opt": []}),
        lambda config: config["services"]["cadvisor"].update({"privileged": False}),
        lambda config: config["services"]["prometheus"].update({"privileged": True}),
        lambda config: config["services"]["newgreedy"].update(
            {"volumes": [{"type": "bind", "target": "/app/config.ini"}]}
        ),
        lambda config: config["services"]["newgreedy"]["volumes"].append(
            {"type": "tmpfs", "target": "/app/stats.json"}
        ),
        lambda config: config["services"]["newgreedy"]["volumes"][0]["bind"].update(
            {"create_host_path": True}
        ),
        lambda config: config["services"]["newgreedy"]["volumes"].__setitem__(
            -1, {"type": "tmpfs", "target": "/root/.mitmproxy"}
        ),
        lambda config: config["services"]["newgreedy-init"].update({"network_mode": "bridge"}),
        lambda config: config["services"]["api"].update({"image": "example.invalid/wos:latest"}),
        lambda config: config["services"]["worker"]["environment"].update(
            {"WOS_INTEGRATION_ACCOUNTS_JSON": ""}
        ),
        lambda config: config["services"]["api"]["environment"].update(
            {"WOS_INTEGRATION_ACCOUNTS_JSON": "secret"}
        ),
        lambda config: config["services"]["api"]["environment"].update(
            {"FORWARDED_ALLOW_IPS": "*"}
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
