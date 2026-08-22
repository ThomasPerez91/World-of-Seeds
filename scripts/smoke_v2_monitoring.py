#!/usr/bin/env python3
"""Exercise the local Prometheus/Grafana profile through its supported boundaries."""

from __future__ import annotations

import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENT = ROOT / ".env.v2.local"
if not ENVIRONMENT.exists():
    ENVIRONMENT = ROOT / ".env.v2.local.example"
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    "world-of-seeds-v2-local",
    "--env-file",
    str(ENVIRONMENT),
    "-f",
    str(ROOT / "compose.v2.yaml"),
    "-f",
    str(ROOT / "compose.v2.local.yaml"),
    "-f",
    str(ROOT / "compose.v2.monitoring.yaml"),
    "--profile",
    "monitoring",
]
ALERTS = {
    "WOSMetricsTargetDown",
    "WOSJobQueueStalled",
    "WOSJobFailures",
    "WOSStorageCritical",
    "WOSQbittorrentUnavailable",
    "WOSRedisUnavailable",
    "WOSApiServerErrors",
    "WOSDatabaseUnavailable",
    "WOSHostCpuSaturated",
    "WOSHostMemorySaturated",
    "WOSHostDiskFilling",
    "WOSHostIoSaturated",
    "WOSContainerRestarting",
}


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in ENVIRONMENT.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _request(url: str, *, authorization: str | None = None) -> Any:
    headers = {"Authorization": authorization} if authorization else {}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _prometheus(path: str) -> Any:
    code = (
        "import urllib.request;"
        f"print(urllib.request.urlopen('http://prometheus:9090{path}',timeout=10)"
        ".read().decode())"
    )
    result = subprocess.run(
        [*COMPOSE, "exec", "-T", "api", "python", "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main() -> int:
    environment = _env()
    port = environment.get("WOS_V2_GRAFANA_PORT", "23000")
    user = environment["WOS_V2_GRAFANA_ADMIN_USER"]
    password = environment["WOS_V2_GRAFANA_ADMIN_PASSWORD"]
    authorization = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    deadline = time.monotonic() + 90
    targets: dict[str, str] = {}
    while time.monotonic() < deadline:
        payload = _prometheus("/api/v1/targets")
        targets = {
            target["labels"]["job"]: target["health"]
            for target in payload["data"]["activeTargets"]
        }
        if all(
            targets.get(job) == "up"
            for job in ("wos-api", "prometheus", "node-exporter", "cadvisor")
        ):
            break
        time.sleep(3)
    else:
        raise RuntimeError(f"Prometheus targets did not become healthy: {targets}")

    rules = _prometheus("/api/v1/rules")
    alert_names = {
        rule["name"]
        for group in rules["data"]["groups"]
        for rule in group["rules"]
        if rule.get("type") == "alerting"
    }
    if not ALERTS.issubset(alert_names):
        raise RuntimeError(f"missing alert rules: {sorted(ALERTS - alert_names)}")

    health = _request(f"http://127.0.0.1:{port}/api/health")
    if health.get("database") != "ok":
        raise RuntimeError(f"Grafana database is not healthy: {health}")
    dashboards = _request(
        f"http://127.0.0.1:{port}/api/search?query=World%20of%20Seeds",
        authorization=authorization,
    )
    if not any(item.get("uid") == "world-of-seeds-v2" for item in dashboards):
        raise RuntimeError("the World of Seeds V2 dashboard was not provisioned")

    print(
        json.dumps(
            {"grafana": "ok", "prometheus_targets": targets, "alerts": len(alert_names)}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
