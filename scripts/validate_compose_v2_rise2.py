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
    "newgreedy-init",
    "newgreedy",
    "newgreedy-ca-export",
    "prometheus",
    "grafana",
    "node-exporter",
    "cadvisor",
}
NETWORKS = {
    "edge",
    "backend",
    "torrent",
    "torrent-egress",
    "monitoring",
    "monitoring-edge",
}
INTERNAL_NETWORKS = {"backend", "torrent", "monitoring", "monitoring-edge"}
VOLUMES = {
    "postgres_v2_data",
    "redis_v2_data",
    "qbittorrent_v2_config",
    "newgreedy_v2_ca",
    "newgreedy_v2_public_ca",
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


def _mounts_by_target(service: Mapping[str, Any], name: str) -> dict[str, Mapping[str, Any]]:
    mounts = _sequence(service.get("volumes"), f"{name}.volumes")
    result: dict[str, Mapping[str, Any]] = {}
    for raw in mounts:
        mount = _mapping(raw, f"{name}.volume")
        target = mount.get("target")
        if not isinstance(target, str) or target in result:
            raise ComposeRise2PolicyError(f"{name} volumes must have unique targets")
        result[target] = mount
    return result


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("name") != "world-of-seeds-v2-rise2":
        raise ComposeRise2PolicyError("Rise2 must use its dedicated project name")
    services = _mapping(config.get("services"), "services")
    if set(services) != SERVICES:
        raise ComposeRise2PolicyError("Rise2 service set is incomplete or unexpected")
    networks = _mapping(config.get("networks"), "networks")
    if set(networks) != NETWORKS:
        raise ComposeRise2PolicyError("Rise2 network set is incomplete or unexpected")
    for name in INTERNAL_NETWORKS:
        if _mapping(networks[name], f"networks.{name}").get("internal") is not True:
            raise ComposeRise2PolicyError(f"{name} must be internal")
    if _mapping(networks["torrent-egress"], "networks.torrent-egress").get("internal") is True:
        raise ComposeRise2PolicyError("torrent-egress must provide external connectivity")
    if set(_mapping(config.get("volumes"), "volumes")) != VOLUMES:
        raise ComposeRise2PolicyError("Rise2 volume set is incomplete or unexpected")

    for name, image in PINNED_IMAGES.items():
        if _mapping(services[name], name).get("image") != image:
            raise ComposeRise2PolicyError(f"{name} image must retain its approved pin")
    for name in (
        "api",
        "worker",
        "scheduler",
        "migrate",
        "newgreedy-init",
        "newgreedy",
        "newgreedy-ca-export",
    ):
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
        "migrate": {"backend"},
        "api": {"edge", "backend"},
        "worker": {"backend", "torrent"},
        "scheduler": {"backend", "torrent"},
        "postgres": {"backend"},
        "redis": {"backend"},
        "qbittorrent-init": {"torrent"},
        "qbittorrent": {"torrent", "torrent-egress"},
        "newgreedy-init": set(),
        "newgreedy": {"torrent", "torrent-egress"},
        "newgreedy-ca-export": set(),
        "prometheus": {"backend", "monitoring"},
        "grafana": {"monitoring", "monitoring-edge"},
        "node-exporter": {"monitoring"},
        "cadvisor": {"monitoring"},
    }
    for name, expected in expected_networks.items():
        if _network_names(_mapping(services[name], name)) != expected:
            raise ComposeRise2PolicyError(f"{name} networks violate Rise2 isolation")

    qbittorrent = _mapping(services["qbittorrent"], "qbittorrent")
    qbittorrent_environment = _mapping(
        qbittorrent.get("environment"), "qbittorrent.environment"
    )
    if qbittorrent_environment.get("UMASK") != "077":
        raise ComposeRise2PolicyError("qBittorrent must retain a private runtime umask")
    if qbittorrent.get("cap_drop") != ["ALL"] or qbittorrent.get("cap_add") != [
        "CHOWN",
        "DAC_OVERRIDE",
        "KILL",
        "SETGID",
        "SETUID",
    ]:
        raise ComposeRise2PolicyError(
            "qBittorrent must retain only the validated runtime and signal-forwarding capabilities"
        )
    qbittorrent_mounts = _mounts_by_target(qbittorrent, "qbittorrent")
    public_ca_mount = qbittorrent_mounts.get("/wos-ca", {})
    if (
        public_ca_mount.get("type") != "volume"
        or not str(public_ca_mount.get("source", "")).endswith("newgreedy_v2_public_ca")
        or public_ca_mount.get("read_only") is not True
    ):
        raise ComposeRise2PolicyError("qBittorrent may mount only the exported public CA volume")
    for mount in qbittorrent_mounts.values():
        if str(mount.get("source", "")).endswith("newgreedy_v2_ca"):
            raise ComposeRise2PolicyError("qBittorrent must never mount the private NewGreedy CA volume")
    entrypoint = list(
        _sequence(qbittorrent.get("entrypoint"), "qbittorrent.entrypoint")
    )
    if entrypoint != ["/bin/sh", "-ec"]:
        raise ComposeRise2PolicyError("qBittorrent CA wrapper entrypoint is missing")
    qbittorrent_command = json.dumps(qbittorrent.get("command", ""))
    for required in (
        "/wos-ca/mitmproxy-ca-cert.pem",
        "/etc/ssl/certs/ca-certificates.crt",
        "PRIVATE KEY",
        "exec /sbin/tini -g -- /entrypoint.sh",
    ):
        if required not in qbittorrent_command:
            raise ComposeRise2PolicyError("qBittorrent public CA bootstrap is incomplete")
    qbittorrent_depends_on = _mapping(
        qbittorrent.get("depends_on"), "qbittorrent.depends_on"
    )
    ca_export_dependency = _mapping(
        qbittorrent_depends_on.get("newgreedy-ca-export"),
        "qbittorrent NewGreedy CA export dependency",
    )
    if ca_export_dependency.get("condition") != "service_completed_successfully":
        raise ComposeRise2PolicyError("qBittorrent must wait for public CA export")

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
    api_environment = _mapping(
        _mapping(services["api"], "api").get("environment"), "api.environment"
    )
    if api_environment.get("FORWARDED_ALLOW_IPS") != "172.30.0.2":
        raise ComposeRise2PolicyError("api must trust only the fixed ingress address")
    ingress_networks = _mapping(ingress.get("networks"), "ingress.networks")
    api_networks = _mapping(_mapping(services["api"], "api").get("networks"), "api.networks")
    ingress_edge = _mapping(ingress_networks.get("edge"), "ingress.networks.edge")
    if ingress_edge.get("ipv4_address") != "172.30.0.2":
        raise ComposeRise2PolicyError("ingress must retain its trusted edge address")
    if _mapping(api_networks.get("edge"), "api.networks.edge").get("ipv4_address") != "172.30.0.3":
        raise ComposeRise2PolicyError("api must retain its fixed edge address")
    edge = _mapping(networks["edge"], "networks.edge")
    ipam = _mapping(edge.get("ipam"), "networks.edge.ipam")
    configs = _sequence(ipam.get("config"), "networks.edge.ipam.config")
    if len(configs) != 1 or _mapping(configs[0], "edge subnet").get("subnet") != "172.30.0.0/24":
        raise ComposeRise2PolicyError("edge must retain its dedicated trusted-proxy subnet")

    newgreedy = _mapping(services["newgreedy"], "newgreedy")
    if (
        newgreedy.get("cap_drop") != ["ALL"]
        or newgreedy.get("privileged") is True
        or newgreedy.get("security_opt") != ["no-new-privileges:true"]
        or newgreedy.get("read_only") is not True
    ):
        raise ComposeRise2PolicyError("NewGreedy runtime hardening is incomplete")
    if newgreedy.get("user") not in (None, ""):
        raise ComposeRise2PolicyError("NewGreedy must retain the validated image root user")
    groups = _sequence(newgreedy.get("group_add"), "newgreedy.group_add")
    if len(groups) != 1 or re.fullmatch(r"[1-9][0-9]*", str(groups[0])) is None:
        raise ComposeRise2PolicyError("NewGreedy may add only its numeric config-reader group")

    mounts = _mounts_by_target(newgreedy, "newgreedy")
    config_mount = mounts.get("/app/config.ini", {})
    if config_mount.get("type") != "bind" or config_mount.get("read_only") is not True:
        raise ComposeRise2PolicyError("NewGreedy config must be one read-only bind")
    config_bind = _mapping(config_mount.get("bind"), "newgreedy config bind")
    # Compose omits an explicit false from normalized JSON. Reject only an
    # explicit true; preflight verifies every source exists before any run.
    if config_bind.get("create_host_path") is True:
        raise ComposeRise2PolicyError("NewGreedy config bind must fail when its source is absent")
    state_targets = {
        "/app/stats.json": "stats.json",
        "/app/torrent_registry.json": "torrent_registry.json",
        "/app/newgreedy.log": "newgreedy.log",
        "/app/purge_pending.json": "purge_pending.json",
    }
    state_parents: set[str] = set()
    for target, filename in state_targets.items():
        mount = mounts.get(target, {})
        source = str(mount.get("source", ""))
        if mount.get("type") != "bind" or mount.get("read_only") is True:
            raise ComposeRise2PolicyError(f"NewGreedy state must be a writable bind: {target}")
        bind = _mapping(mount.get("bind"), f"NewGreedy state bind: {target}")
        if bind.get("create_host_path") is True:
            raise ComposeRise2PolicyError(f"NewGreedy state bind must fail if absent: {target}")
        if not source.endswith(f"/{filename}"):
            raise ComposeRise2PolicyError(f"NewGreedy state source is invalid: {target}")
        state_parents.add(source[: -len(filename) - 1])
    if len(state_parents) != 1:
        raise ComposeRise2PolicyError("NewGreedy state files must share one persistent directory")
    ca_mount = mounts.get("/root/.mitmproxy", {})
    if (
        ca_mount.get("type") != "volume"
        or not str(ca_mount.get("source", "")).endswith("newgreedy_v2_ca")
        or ca_mount.get("read_only") is True
    ):
        raise ComposeRise2PolicyError("NewGreedy CA must use its writable persistent volume")
    forbidden_tmpfs = set(state_targets) | {"/app/config.ini", "/root/.mitmproxy"}
    for item in _sequence(newgreedy.get("tmpfs", []), "newgreedy.tmpfs"):
        if str(item).split(":", 1)[0] in forbidden_tmpfs:
            raise ComposeRise2PolicyError("NewGreedy persistent paths must not use tmpfs")
    healthcheck = _mapping(newgreedy.get("healthcheck"), "newgreedy.healthcheck")
    health_test = list(_sequence(healthcheck.get("test"), "newgreedy.healthcheck.test"))
    if health_test != ["CMD", "curl", "-fsS", "http://127.0.0.1:8080/api/health"]:
        raise ComposeRise2PolicyError("NewGreedy healthcheck must use the bundled curl client")
    depends_on = _mapping(newgreedy.get("depends_on"), "newgreedy.depends_on")
    init_dependency = _mapping(depends_on.get("newgreedy-init"), "newgreedy init dependency")
    if init_dependency.get("condition") != "service_completed_successfully":
        raise ComposeRise2PolicyError("NewGreedy must wait for successful state initialization")

    newgreedy_init = _mapping(services["newgreedy-init"], "newgreedy-init")
    if newgreedy_init.get("image") != newgreedy.get("image"):
        raise ComposeRise2PolicyError("NewGreedy init must use the exact runtime image digest")
    if (
        newgreedy_init.get("network_mode") != "none"
        or newgreedy_init.get("cap_drop") != ["ALL"]
        or newgreedy_init.get("security_opt") != ["no-new-privileges:true"]
        or newgreedy_init.get("read_only") is not True
        or newgreedy_init.get("privileged") is True
    ):
        raise ComposeRise2PolicyError("NewGreedy init hardening is incomplete")
    init_mounts = _mounts_by_target(newgreedy_init, "newgreedy-init")
    state_mount = init_mounts.get("/state", {})
    if state_mount.get("type") != "bind" or state_mount.get("read_only") is True:
        raise ComposeRise2PolicyError("NewGreedy init requires one writable state bind")
    state_bind = _mapping(state_mount.get("bind"), "newgreedy init state bind")
    if state_bind.get("create_host_path") is True:
        raise ComposeRise2PolicyError("NewGreedy init state bind must fail when absent")
    if str(state_mount.get("source", "")) not in state_parents:
        raise ComposeRise2PolicyError("NewGreedy init and runtime must share the state directory")
    command = json.dumps(newgreedy_init.get("command", ""))
    for filename in state_targets.values():
        if filename not in command:
            raise ComposeRise2PolicyError(f"NewGreedy init must prepare {filename}")

    ca_export = _mapping(services["newgreedy-ca-export"], "newgreedy-ca-export")
    if ca_export.get("image") != newgreedy.get("image"):
        raise ComposeRise2PolicyError("NewGreedy CA export must use the runtime image digest")
    if (
        ca_export.get("network_mode") != "none"
        or ca_export.get("cap_drop") != ["ALL"]
        or ca_export.get("security_opt") != ["no-new-privileges:true"]
        or ca_export.get("read_only") is not True
        or ca_export.get("privileged") is True
    ):
        raise ComposeRise2PolicyError("NewGreedy CA export hardening is incomplete")
    export_mounts = _mounts_by_target(ca_export, "newgreedy-ca-export")
    if set(export_mounts) != {"/private", "/public"}:
        raise ComposeRise2PolicyError("NewGreedy CA export mounts are unexpected")
    private_mount = export_mounts["/private"]
    if (
        private_mount.get("type") != "volume"
        or not str(private_mount.get("source", "")).endswith("newgreedy_v2_ca")
        or private_mount.get("read_only") is not True
    ):
        raise ComposeRise2PolicyError("CA export source must be the read-only private CA volume")
    exported_mount = export_mounts["/public"]
    if (
        exported_mount.get("type") != "volume"
        or not str(exported_mount.get("source", "")).endswith("newgreedy_v2_public_ca")
        or exported_mount.get("read_only") is True
    ):
        raise ComposeRise2PolicyError("CA export target must be the writable public CA volume")
    export_command = json.dumps(ca_export.get("command", ""))
    for required in (
        "/private/mitmproxy-ca-cert.pem",
        "/public/mitmproxy-ca-cert.pem",
        "PRIVATE KEY",
    ):
        if required not in export_command:
            raise ComposeRise2PolicyError("NewGreedy public CA export is incomplete")
    if "mitmproxy-ca.pem" in export_command:
        raise ComposeRise2PolicyError("CA export must never copy the private-key-bearing CA file")
    export_depends_on = _mapping(ca_export.get("depends_on"), "newgreedy-ca-export.depends_on")
    newgreedy_dependency = _mapping(
        export_depends_on.get("newgreedy"), "NewGreedy CA export dependency"
    )
    if newgreedy_dependency.get("condition") != "service_healthy":
        raise ComposeRise2PolicyError("CA export must wait for healthy NewGreedy")

    payload = json.dumps(config, sort_keys=True)
    for forbidden in (
        "/srv/seedbox",
        "local-test-passkey",
        "chmod 777",
        "docker.sock:/",
    ):
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
