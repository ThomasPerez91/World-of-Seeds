#!/usr/bin/env python3
"""Validate the normalized, workstation-only V2 Compose profile from stdin."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

QBITTORRENT_IMAGE = "qbittorrentofficial/qbittorrent-nox:5.2.3-1"
SERVICES = {
    "api",
    "worker",
    "scheduler",
    "postgres",
    "redis",
    "newgreedy",
    "qbittorrent-init",
    "qbittorrent",
}
PRIVATE_SERVICES = SERVICES - {"api"}
VOLUMES = {
    "postgres_v2_data",
    "redis_v2_data",
    "storage_v2_local",
    "qbittorrent_v2_local_config",
}


class ComposeLocalPolicyError(RuntimeError):
    """Raised when the normalized local profile breaks a safety invariant."""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ComposeLocalPolicyError(f"{name} must be an object")
    return value


def _volume_types(service: Mapping[str, Any]) -> set[str]:
    mounts = service.get("volumes", [])
    if not isinstance(mounts, Sequence) or isinstance(mounts, (str, bytes)):
        raise ComposeLocalPolicyError("service volumes must be a list")
    result: set[str] = set()
    for mount in mounts:
        result.add(str(_mapping(mount, "volume mount").get("type")))
    return result


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("name") != "world-of-seeds-v2-local":
        raise ComposeLocalPolicyError("local V2 must use its dedicated project name")
    services = _mapping(config.get("services"), "services")
    if set(services) != SERVICES:
        raise ComposeLocalPolicyError("local V2 service set is incomplete or unexpected")

    for name in PRIVATE_SERVICES:
        service = _mapping(services[name], f"services.{name}")
        if service.get("ports"):
            raise ComposeLocalPolicyError(f"{name} must not publish a host port")
        networks = set(_mapping(service.get("networks"), f"services.{name}.networks"))
        if networks != {"backend"}:
            raise ComposeLocalPolicyError(f"{name} must use only the private backend network")

    api = _mapping(services["api"], "services.api")
    ports = api.get("ports")
    if not isinstance(ports, list) or len(ports) != 1:
        raise ComposeLocalPolicyError("api must publish exactly one port")
    port = _mapping(ports[0], "services.api.ports[0]")
    if port.get("host_ip") != "127.0.0.1" or port.get("target") != 8000:
        raise ComposeLocalPolicyError("api must bind target 8000 to host loopback")

    for name in ("api", "worker", "scheduler"):
        service = _mapping(services[name], f"services.{name}")
        if str(service.get("user")) != "10001:10001":
            raise ComposeLocalPolicyError(f"{name} must use the image-owned uid/gid")
        types = _volume_types(service)
        if "bind" in types:
            raise ComposeLocalPolicyError(f"{name} must not use a host bind mount")
        environment = _mapping(service.get("environment"), f"services.{name}.environment")
        if environment.get("WOS_ENVIRONMENT") != "development":
            raise ComposeLocalPolicyError(f"{name} local helpers require development mode")

    if _mapping(services["scheduler"], "scheduler").get("command") != [
        "python",
        "-m",
        "app.scheduler_service",
    ]:
        raise ComposeLocalPolicyError("scheduler must use its durable entry point")
    if _mapping(services["worker"], "worker").get("command") != [
        "python",
        "-m",
        "app.worker",
    ]:
        raise ComposeLocalPolicyError("worker must use its durable entry point")

    for name in ("qbittorrent", "qbittorrent-init"):
        if _mapping(services[name], name).get("image") != QBITTORRENT_IMAGE:
            raise ComposeLocalPolicyError("qBittorrent must use the approved multi-arch pin")
    if not _mapping(services["qbittorrent"], "qbittorrent").get("healthcheck"):
        raise ComposeLocalPolicyError("qBittorrent must define a healthcheck")
    if not _mapping(services["newgreedy"], "newgreedy").get("healthcheck"):
        raise ComposeLocalPolicyError("NewGreedy fixture must define a healthcheck")

    backend = _mapping(_mapping(config.get("networks"), "networks").get("backend"), "backend")
    if backend.get("internal") is not True:
        raise ComposeLocalPolicyError("backend network must remain internal")
    if set(_mapping(config.get("volumes"), "volumes")) != VOLUMES:
        raise ComposeLocalPolicyError("local V2 must use only its four named volumes")

    serialized = json.dumps(config, sort_keys=True)
    for forbidden in ("/var/run/docker.sock", '"type": "bind"', "/srv/"):
        if forbidden in serialized:
            raise ComposeLocalPolicyError(f"forbidden local profile value: {forbidden}")


def main() -> int:
    try:
        validate_config(_mapping(json.load(sys.stdin), "compose config"))
    except (ComposeLocalPolicyError, json.JSONDecodeError) as exc:
        print(f"V2 local Compose validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
