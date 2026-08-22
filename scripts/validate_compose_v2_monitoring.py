#!/usr/bin/env python3
"""Validate the normalized V2 monitoring Compose overlay from stdin."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

IMAGES = {
    "prometheus": "prom/prometheus:v3.14.0",
    "grafana": "grafana/grafana:13.2.0",
    "node-exporter": "prom/node-exporter:v1.12.1",
    "cadvisor": "gcr.io/cadvisor/cadvisor:v0.60.0",
}
MONITORING_SERVICES = set(IMAGES)


class ComposeMonitoringPolicyError(RuntimeError):
    """Raised when the normalized monitoring profile breaks an invariant."""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ComposeMonitoringPolicyError(f"{name} must be an object")
    return value


def _mounts(service: Mapping[str, Any]) -> Sequence[object]:
    mounts = service.get("volumes", [])
    if not isinstance(mounts, Sequence) or isinstance(mounts, (str, bytes)):
        raise ComposeMonitoringPolicyError("service volumes must be a list")
    return mounts


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("name") != "world-of-seeds-v2-local":
        raise ComposeMonitoringPolicyError(
            "monitoring must use the isolated local project"
        )
    services = _mapping(config.get("services"), "services")
    if not MONITORING_SERVICES.issubset(services):
        raise ComposeMonitoringPolicyError("monitoring service set is incomplete")

    for name, image in IMAGES.items():
        service = _mapping(services[name], f"services.{name}")
        if service.get("image") != image:
            raise ComposeMonitoringPolicyError(
                f"{name} must use the approved immutable tag"
            )
        if service.get("profiles") != ["monitoring"]:
            raise ComposeMonitoringPolicyError(
                f"{name} must require the monitoring profile"
            )

    networks = _mapping(config.get("networks"), "networks")
    monitoring = _mapping(networks.get("monitoring"), "networks.monitoring")
    if monitoring.get("internal") is not True:
        raise ComposeMonitoringPolicyError("monitoring network must be internal")

    prometheus = _mapping(services["prometheus"], "services.prometheus")
    if set(_mapping(prometheus.get("networks"), "prometheus.networks")) != {
        "backend",
        "monitoring",
    }:
        raise ComposeMonitoringPolicyError(
            "only Prometheus may bridge backend and monitoring"
        )
    command = prometheus.get("command", [])
    if not isinstance(command, list) or not any(
        str(item).startswith("--storage.tsdb.retention.time=") for item in command
    ):
        raise ComposeMonitoringPolicyError("Prometheus retention must be explicit")

    for name in ("grafana", "node-exporter", "cadvisor"):
        service = _mapping(services[name], f"services.{name}")
        if set(_mapping(service.get("networks"), f"services.{name}.networks")) != {
            "monitoring"
        }:
            raise ComposeMonitoringPolicyError(f"{name} must use only monitoring")
        if name != "grafana" and service.get("ports"):
            raise ComposeMonitoringPolicyError(f"{name} must not publish a host port")

    grafana = _mapping(services["grafana"], "services.grafana")
    ports = grafana.get("ports")
    if not isinstance(ports, list) or len(ports) != 1:
        raise ComposeMonitoringPolicyError("Grafana must publish exactly one port")
    port = _mapping(ports[0], "services.grafana.ports[0]")
    if port.get("host_ip") != "127.0.0.1" or port.get("target") != 3000:
        raise ComposeMonitoringPolicyError(
            "Grafana must bind target 3000 to host loopback"
        )
    environment = _mapping(grafana.get("environment"), "grafana.environment")
    if environment.get("GF_AUTH_ANONYMOUS_ENABLED") != "false":
        raise ComposeMonitoringPolicyError("Grafana anonymous access must be disabled")
    for key in ("GF_SECURITY_ADMIN_USER", "GF_SECURITY_ADMIN_PASSWORD"):
        if not environment.get(key):
            raise ComposeMonitoringPolicyError(f"Grafana requires {key}")

    for name in MONITORING_SERVICES:
        for raw_mount in _mounts(_mapping(services[name], name)):
            mount = _mapping(raw_mount, f"services.{name}.volume")
            if mount.get("type") == "bind" and mount.get("read_only") is not True:
                raise ComposeMonitoringPolicyError(
                    f"{name} bind mounts must be read-only"
                )
            source = str(mount.get("source", ""))
            if name != "cadvisor" and source in {"/var/run", "/var/run/docker.sock"}:
                raise ComposeMonitoringPolicyError(
                    "only cAdvisor may inspect the runtime"
                )

    monitoring_payload = json.dumps(
        {name: services[name] for name in MONITORING_SERVICES}, sort_keys=True
    )
    for forbidden in (
        "local-test-passkey",
        "WOS_INTEGRATION_ACCOUNTS_JSON",
        "/downloads",
    ):
        if forbidden in monitoring_payload:
            raise ComposeMonitoringPolicyError(
                f"monitoring contains application data: {forbidden}"
            )


def main() -> int:
    try:
        validate_config(_mapping(json.load(sys.stdin), "compose config"))
    except (ComposeMonitoringPolicyError, json.JSONDecodeError) as exc:
        print(f"V2 monitoring Compose validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
