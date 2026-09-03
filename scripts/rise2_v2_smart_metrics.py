#!/usr/bin/env python3
"""Publish bounded SMART health metrics for Rise2 node-exporter textfile collection."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _run_json(arguments: list[str]) -> dict[str, Any]:
    result = subprocess.run(
        ["smartctl", *arguments, "-j"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if not result.stdout.strip():
        raise RuntimeError(f"smartctl returned no JSON for {' '.join(arguments)}")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("smartctl JSON root must be an object")
    return payload


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _nested_number(payload: dict[str, Any], *path: str) -> float | None:
    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return _number(current)


def _ata_raw(payload: dict[str, Any], attribute_id: int) -> float | None:
    table = payload.get("ata_smart_attributes", {}).get("table", [])
    if not isinstance(table, list):
        return None
    for item in table:
        if not isinstance(item, dict) or item.get("id") != attribute_id:
            continue
        raw = item.get("raw", {})
        if isinstance(raw, dict):
            return _number(raw.get("value"))
    return None


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _metric(lines: list[str], name: str, device: str, value: float | None) -> None:
    if value is None:
        return
    lines.append(f'{name}{{device="{_label(device)}"}} {value:g}')


def _device_metrics(payload: dict[str, Any], device: str) -> list[str]:
    lines: list[str] = []
    smart_passed = payload.get("smart_status", {}).get("passed")
    if isinstance(smart_passed, bool):
        _metric(lines, "wos_smart_device_healthy", device, float(int(smart_passed)))

    temperature = _nested_number(payload, "temperature", "current")
    if temperature is None:
        temperature = _nested_number(payload, "nvme_smart_health_information_log", "temperature")
    _metric(lines, "wos_smart_temperature_celsius", device, temperature)

    power_on_hours = _nested_number(payload, "power_on_time", "hours")
    if power_on_hours is None:
        power_on_hours = _ata_raw(payload, 9)
    _metric(lines, "wos_smart_power_on_hours", device, power_on_hours)

    _metric(lines, "wos_smart_reallocated_sectors", device, _ata_raw(payload, 5))
    _metric(lines, "wos_smart_current_pending_sectors", device, _ata_raw(payload, 197))
    _metric(lines, "wos_smart_offline_uncorrectable_sectors", device, _ata_raw(payload, 198))

    nvme = payload.get("nvme_smart_health_information_log", {})
    if isinstance(nvme, dict):
        _metric(lines, "wos_smart_nvme_percentage_used", device, _number(nvme.get("percentage_used")))
        _metric(lines, "wos_smart_nvme_media_errors_total", device, _number(nvme.get("media_errors")))
        _metric(
            lines,
            "wos_smart_nvme_unsafe_shutdowns_total",
            device,
            _number(nvme.get("unsafe_shutdowns")),
        )
    return lines


def collect() -> tuple[list[str], int]:
    scan = _run_json(["--scan-open"])
    devices = scan.get("devices", [])
    if not isinstance(devices, list) or not devices:
        raise RuntimeError("smartctl did not discover any devices")

    lines = [
        "# HELP wos_smart_collect_success Whether the latest SMART collection completed.",
        "# TYPE wos_smart_collect_success gauge",
        "wos_smart_collect_success 1",
        "# HELP wos_smart_device_healthy SMART overall-health result per physical device.",
        "# TYPE wos_smart_device_healthy gauge",
        "# HELP wos_smart_temperature_celsius Current SMART device temperature.",
        "# TYPE wos_smart_temperature_celsius gauge",
        "# HELP wos_smart_power_on_hours SMART power-on hours.",
        "# TYPE wos_smart_power_on_hours gauge",
        "# HELP wos_smart_reallocated_sectors ATA reallocated-sector count.",
        "# TYPE wos_smart_reallocated_sectors gauge",
        "# HELP wos_smart_current_pending_sectors ATA current-pending-sector count.",
        "# TYPE wos_smart_current_pending_sectors gauge",
        "# HELP wos_smart_offline_uncorrectable_sectors ATA offline-uncorrectable count.",
        "# TYPE wos_smart_offline_uncorrectable_sectors gauge",
        "# HELP wos_smart_nvme_percentage_used NVMe percentage-used health indicator.",
        "# TYPE wos_smart_nvme_percentage_used gauge",
        "# HELP wos_smart_nvme_media_errors_total NVMe media/data-integrity errors.",
        "# TYPE wos_smart_nvme_media_errors_total gauge",
        "# HELP wos_smart_nvme_unsafe_shutdowns_total NVMe unsafe shutdown count.",
        "# TYPE wos_smart_nvme_unsafe_shutdowns_total gauge",
    ]

    collected = 0
    for raw in devices:
        if not isinstance(raw, dict):
            continue
        device = raw.get("name")
        if not isinstance(device, str) or not device.startswith("/dev/"):
            continue
        arguments = ["-H", "-A"]
        device_type = raw.get("type")
        if isinstance(device_type, str) and device_type and device_type != "auto":
            arguments.extend(["-d", device_type])
        arguments.append(device)
        try:
            payload = _run_json(arguments)
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, RuntimeError):
            continue
        lines.extend(_device_metrics(payload, device))
        collected += 1

    if collected == 0:
        raise RuntimeError("SMART collection failed for every discovered device")
    lines.append(f"wos_smart_devices_collected {collected}")
    return lines, collected


def write_metrics(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines, _ = collect()
    payload = "\n".join(lines) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/var/lib/world-of-seeds-v2/node-exporter-textfile/wos_smart.prom"),
    )
    args = parser.parse_args()
    try:
        write_metrics(args.output)
    except Exception as exc:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            "# HELP wos_smart_collect_success Whether the latest SMART collection completed.\n"
            "# TYPE wos_smart_collect_success gauge\n"
            "wos_smart_collect_success 0\n",
            encoding="utf-8",
        )
        os.chmod(args.output, 0o644)
        print(f"SMART metrics collection failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
