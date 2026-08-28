#!/usr/bin/env python3
"""Validate the normalized Docker Compose V2 foundation from standard input."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

POSTGRES_IMAGE = "postgres:17.11-alpine3.24"
REDIS_IMAGE = "redis:8.2.9-alpine3.22"


class ComposePolicyError(RuntimeError):
    """Raised when the normalized V2 Compose configuration breaks an invariant."""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ComposePolicyError(f"{name} must be an object")
    return value


def validate_config(config: Mapping[str, Any]) -> None:
    if config.get("name") != "world-of-seeds-v2":
        raise ComposePolicyError("the V2 project name must stay isolated")

    services = _mapping(config.get("services"), "services")
    if set(services) != {"api", "worker", "postgres", "redis"}:
        raise ComposePolicyError(
            "the V2 foundation must contain only api/worker/postgres/redis"
        )

    api = _mapping(services["api"], "services.api")
    worker = _mapping(services["worker"], "services.worker")
    postgres = _mapping(services["postgres"], "services.postgres")
    redis = _mapping(services["redis"], "services.redis")

    if postgres.get("image") != POSTGRES_IMAGE:
        raise ComposePolicyError("PostgreSQL must use the approved exact image tag")
    if redis.get("image") != REDIS_IMAGE:
        raise ComposePolicyError("Redis must use the approved exact image tag")

    for name, service in (("postgres", postgres), ("redis", redis)):
        if service.get("ports"):
            raise ComposePolicyError(f"{name} must not publish a host port")
        if set(_mapping(service.get("networks"), f"services.{name}.networks")) != {
            "backend"
        }:
            raise ComposePolicyError(
                f"{name} must use only the private backend network"
            )
        if not service.get("healthcheck"):
            raise ComposePolicyError(f"{name} must define a healthcheck")

    api_networks = set(_mapping(api.get("networks"), "services.api.networks"))
    if api_networks != {"edge", "backend"}:
        raise ComposePolicyError("api must use only the edge and backend networks")
    if set(_mapping(worker.get("networks"), "services.worker.networks")) != {"backend"}:
        raise ComposePolicyError("worker must use only the private backend network")
    if worker.get("ports"):
        raise ComposePolicyError("worker must not publish a host port")
    if worker.get("command") != ["python", "-m", "app.worker"]:
        raise ComposePolicyError("worker must use the dedicated worker entry point")
    if not api.get("healthcheck"):
        raise ComposePolicyError("api must define a healthcheck")
    api_environment = _mapping(api.get("environment"), "services.api.environment")
    if str(api_environment.get("WOS_API_PROCESS_COUNT")) != "1":
        raise ComposePolicyError("api must use exactly one process")
    if api_environment.get("WOS_RUNTIME_PROFILE") != "v2":
        raise ComposePolicyError("api must use the V2 runtime profile")
    if "WOS_INTEGRATION_ACCOUNTS_JSON" not in api_environment:
        raise ComposePolicyError("api must accept the deployment integration registry")
    if api_environment.get("WOS_REDIS_URL") != "redis://redis:6379/0":
        raise ComposePolicyError("api must use only the internal V2 Redis service")
    worker_environment = _mapping(
        worker.get("environment"), "services.worker.environment"
    )
    if worker_environment.get("WOS_REDIS_URL") != "redis://redis:6379/0":
        raise ComposePolicyError("worker must use only the internal V2 Redis service")
    if worker_environment.get("WOS_RUNTIME_PROFILE") != "v2":
        raise ComposePolicyError("worker must use the V2 runtime profile")
    for key in (
        "WOS_COOKIE_SECURE",
        "WOS_ALLOWED_HOSTS",
        "WOS_DATA_ROOT",
        "WOS_QBITTORRENT_DATA_ROOT",
        "WOS_INTEGRATION_ACCOUNTS_JSON",
    ):
        if key not in worker_environment:
            raise ComposePolicyError(
                "worker production runtime settings are incomplete"
            )
    if api.get("command") is not None:
        raise ComposePolicyError("api must retain the image single-process entry point")

    dependencies = _mapping(api.get("depends_on"), "services.api.depends_on")
    for dependency in ("postgres", "redis"):
        policy = _mapping(dependencies.get(dependency), f"api dependency {dependency}")
        if policy.get("condition") != "service_healthy":
            raise ComposePolicyError(f"api must wait for healthy {dependency}")
        worker_policy = _mapping(
            _mapping(worker.get("depends_on"), "services.worker.depends_on").get(
                dependency
            ),
            f"worker dependency {dependency}",
        )
        if worker_policy.get("condition") != "service_healthy":
            raise ComposePolicyError(f"worker must wait for healthy {dependency}")

    ports = api.get("ports")
    if not isinstance(ports, list) or len(ports) != 1:
        raise ComposePolicyError("api must publish exactly one loopback port")
    port = _mapping(ports[0], "services.api.ports[0]")
    if port.get("host_ip") != "127.0.0.1" or port.get("target") != 8000:
        raise ComposePolicyError("api port must bind target 8000 to host loopback")

    networks = _mapping(config.get("networks"), "networks")
    backend = _mapping(networks.get("backend"), "networks.backend")
    if backend.get("internal") is not True:
        raise ComposePolicyError("the backend network must be internal")

    volumes = _mapping(config.get("volumes"), "volumes")
    if set(volumes) != {"postgres_v2_data", "redis_v2_data"}:
        raise ComposePolicyError("PostgreSQL and Redis must use dedicated V2 volumes")

    serialized = json.dumps(config, sort_keys=True)
    if "/var/run/docker.sock" in serialized:
        raise ComposePolicyError("the V2 stack must not mount the Docker socket")


def main() -> int:
    try:
        document = json.load(sys.stdin)
        validate_config(_mapping(document, "compose config"))
    except (ComposePolicyError, json.JSONDecodeError) as exc:
        print(f"V2 Compose validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
