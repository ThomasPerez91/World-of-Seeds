#!/usr/bin/env python3
"""Validate the normalized, production-only Rise2 V2 Compose stack from stdin."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

SERVICES = {
    "ingress",
    "migrate",
    "api",
    "worker",
    "scheduler",
    "postgres",
    "redis",
    "qbittorrent-init",
    "qbittorrent",
    "newgreedy",
    "prometheus",
    "grafana",
    "node-exporter",
    "cadvisor",
}
NETWORKS = {"edge", "backend", "torrent", "monitoring", "monitoring-edge"}
VOLUMES = {
    "postgres_v2_data",
    "redis_v2_data",
    "qbittorrent_v2_config",
    "newgreedy_v2_data",
    "prometheus_v2_data",
    "grafana_v2_data",
    "caddy_v2_data",
    "caddy_v2_config",
}
PINNED_IMAGES = {
    "ingress": "caddy:2.10.2-alpine",
    "postgres": "postgres:17.11-alpine3.24",
    "redis": "redis:8.2.9-alpine3.22",
    "qbittorrent": "qbittorrentofficial/qbittorrent-nox:5.2.3-1",
    "qbittorrent-init": "qbittorrentofficial/qbittorrent-nox:5.2.3-1",
    "prometheus": "prom/prometheus:v3.14.0",
    "grafana": "grafana/grafana:13.2.0",
    "node-exporter": "prom/node-exporter:v1.12.1",
    "cadvisor": "ghcr.io/google/cadvisor:v0.60.5",
}
DIGEST_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


class ComposeRise2PolicyError(RuntimeError):
    """The normalized Rise2 stack violates an isolation or security invariant."""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ComposeRise2PolicyError(f"{name} must be an object")
    return value


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ComposeRise2PolicyError(f"{name} must be a list")
    return value


def _network_names(service: Mapping[str, Any]) -> set[str]:
    value = service.get("networks", {})
    if isinstance(value, dict):
        return set(value)
    return {str(item) for item in _sequence(value, "service.networks")}


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("name") != "world-of-seeds-v2-rise2":
        raise ComposeRise2PolicyError("Rise2 must use its dedicated project name")
    services = _mapping(config.get("services"), "services")
    if set(services) != SERVICES:
        raise ComposeRise2PolicyError("Rise2 service set is incomplete or unexpected")
    networks = _mapping(config.get("networks"), "networks")
    if set(networks) != NETWORKS:
        raise ComposeRise2PolicyError("Rise2 network set is incomplete or unexpected")
    for name in NETWORKS - {"edge"}:
        if _mapping(networks[name], f"networks.{name}").get("internal") is not True:
            raise ComposeRise2PolicyError(f"{name} must be internal")
    if set(_mapping(config.get("volumes"), "volumes")) != VOLUMES:
        raise ComposeRise2PolicyError("Rise2 volume set is incomplete or unexpected")

    for name, image in PINNED_IMAGES.items():
        if _mapping(services[name], name).get("image") != image:
            raise ComposeRise2PolicyError(f"{name} image must retain its approved pin")
    for name in ("api", "worker", "scheduler", "migrate", "newgreedy"):
        image = str(_mapping(services[name], name).get("image", ""))
        if not DIGEST_IMAGE.fullmatch(image):
            raise ComposeRise2PolicyError(f"{name} image must be immutable by digest")

    ingress = _mapping(services["ingress"], "ingress")
    if _network_names(ingress) != {"edge", "monitoring-edge"}:
        raise ComposeRise2PolicyError("ingress may bridge only edge and monitoring-edge")
    if ingress.get("cap_drop") != ["ALL"] or ingress.get("cap_add") != ["NET_BIND_SERVICE"]:
        raise ComposeRise2PolicyError("ingress may retain only NET_BIND_SERVICE")
    for name, raw in services.items():
        service = _mapping(raw, name)
        ports = service.get("ports")
        if name == "ingress":
            if not isinstance(ports, list) or len(ports) != 3:
                raise ComposeRise2PolicyError("ingress must publish HTTP and HTTPS only")
        elif ports:
            raise ComposeRise2PolicyError(f"{name} must not publish a host port")

    expected_networks = {
        "api": {"edge", "backend"},
        "worker": {"backend", "torrent"},
        "scheduler": {"backend", "torrent"},
        "postgres": {"backend"},
        "redis": {"backend"},
        "qbittorrent": {"torrent"},
        "newgreedy": {"torrent"},
        "prometheus": {"backend", "monitoring"},
        "grafana": {"monitoring", "monitoring-edge"},
        "node-exporter": {"monitoring"},
        "cadvisor": {"monitoring"},
    }
    for name, expected in expected_networks.items():
        if _network_names(_mapping(services[name], name)) != expected:
            raise ComposeRise2PolicyError(f"{name} networks violate Rise2 isolation")

    for name, raw in services.items():
        privileged = _mapping(raw, name).get("privileged") is True
        if name == "cadvisor" and not privileged:
            raise ComposeRise2PolicyError("cAdvisor requires its approved privileged mode")
        if name != "cadvisor" and privileged:
            raise ComposeRise2PolicyError("only cAdvisor may be privileged")

    for name in ("api", "worker", "scheduler", "migrate"):
        service = _mapping(services[name], name)
        environment = _mapping(service.get("environment"), f"{name}.environment")
        if environment.get("WOS_ENVIRONMENT") != "production":
            raise ComposeRise2PolicyError(f"{name} must use production mode")
        if environment.get("WOS_RUNTIME_PROFILE") != "v2":
            raise ComposeRise2PolicyError(f"{name} must use the V2 runtime profile")
        if environment.get("WOS_COOKIE_SECURE") != "true":
            raise ComposeRise2PolicyError(f"{name} must require secure cookies")
        if environment.get("WOS_REDIS_URL") != "redis://redis:6379/0":
            raise ComposeRise2PolicyError(f"{name} must use internal Redis")
        registry = environment.get("WOS_INTEGRATION_ACCOUNTS_JSON")
        if name in {"worker", "scheduler"} and not registry:
            raise ComposeRise2PolicyError(f"{name} requires the integration registry")
        if name in {"api", "migrate"} and registry:
            raise ComposeRise2PolicyError(f"{name} must not receive integration credentials")
        if service.get("read_only") is not True or service.get("cap_drop") != ["ALL"]:
            raise ComposeRise2PolicyError(f"{name} runtime hardening is incomplete")
    if _mapping(services["api"], "api").get("command") is not None:
        raise ComposeRise2PolicyError("api must retain the measured single-process entry point")
    api_environment = _mapping(_mapping(services["api"], "api").get("environment"), "api.environment")
    if api_environment.get("FORWARDED_ALLOW_IPS") != "172.30.0.2":
        raise ComposeRise2PolicyError("api must trust only the fixed ingress address")
    ingress_networks = _mapping(ingress.get("networks"), "ingress.networks")
    api_networks = _mapping(_mapping(services["api"], "api").get("networks"), "api.networks")
    if _mapping(ingress_networks.get("edge"), "ingress.networks.edge").get("ipv4_address") != "172.30.0.2":
        raise ComposeRise2PolicyError("ingress must retain its trusted edge address")
    if _mapping(api_networks.get("edge"), "api.networks.edge").get("ipv4_address") != "172.30.0.3":
        raise ComposeRise2PolicyError("api must retain its fixed edge address")
    edge = _mapping(networks["edge"], "networks.edge")
    ipam = _mapping(edge.get("ipam"), "networks.edge.ipam")
    configs = _sequence(ipam.get("config"), "networks.edge.ipam.config")
    if len(configs) != 1 or _mapping(configs[0], "edge subnet").get("subnet") != "172.30.0.0/24":
        raise ComposeRise2PolicyError("edge must retain its dedicated trusted-proxy subnet")

    newgreedy = _mapping(services["newgreedy"], "newgreedy")
    if newgreedy.get("cap_drop") != ["ALL"] or newgreedy.get("privileged") is True:
        raise ComposeRise2PolicyError("NewGreedy must remain unprivileged")
    mounts = _sequence(newgreedy.get("volumes"), "newgreedy.volumes")
    config_mounts = [
        _mapping(item, "newgreedy.volume")
        for item in mounts
        if isinstance(item, dict) and item.get("target") == "/app/config.ini"
    ]
    if len(config_mounts) != 1 or config_mounts[0].get("read_only") is not True:
        raise ComposeRise2PolicyError("NewGreedy config must be one read-only bind")

    payload = json.dumps(config, sort_keys=True)
    for forbidden in ("/srv/seedbox", "local-test-passkey", "chmod 777", "docker.sock:/"):
        if forbidden in payload:
            raise ComposeRise2PolicyError(f"Rise2 reuses forbidden V1 or unsafe state: {forbidden}")


def main() -> int:
    try:
        validate_config(_mapping(json.load(sys.stdin), "compose config"))
    except (ComposeRise2PolicyError, json.JSONDecodeError) as exc:
        print(f"Rise2 V2 Compose validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
