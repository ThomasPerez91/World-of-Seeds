#!/usr/bin/env python3
"""Run V2-33 Rise2 scheduler load gates 3 and 4 in one controlled window."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")
LOAD_SCHEMA = "world-of-seeds-v2-rise2-scheduler-load/v1"
PROM_SCHEMA = "world-of-seeds-v2-rise2-load-prometheus/v1"
SUMMARY_SCHEMA = "world-of-seeds-v2-rise2-load-runner/v1"

IN_CONTAINER_LAUNCHER = r'''
import os
import sys
from pathlib import Path
registry = Path("/run/secrets/integration_registry").read_text(encoding="utf-8")
if not registry or len(registry) > 1024 * 1024:
    raise SystemExit("invalid integration registry")
os.environ["WOS_INTEGRATION_ACCOUNTS_JSON"] = registry
os.execv(
    sys.executable,
    [sys.executable, "/bootstrap/rise2_v2_scheduler_load_runtime.py", *sys.argv[1:]],
)
'''

PROMETHEUS_COLLECTOR = r'''
import json
import math
import sys
import urllib.parse
import urllib.request

start = int(sys.argv[1])
end = int(sys.argv[2])
base = "http://prometheus:9090"
queries = {
    "host_cpu_peak_ratio": (
        '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))',
        max,
    ),
    "host_iowait_peak_ratio": (
        'avg(rate(node_cpu_seconds_total{mode="iowait"}[1m]))',
        max,
    ),
    "host_memory_peak_ratio": (
        "1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)",
        max,
    ),
    "host_load1_peak": ("node_load1", max),
}
result = {
    "schema": "world-of-seeds-v2-rise2-load-prometheus/v1",
    "start_epoch": start,
    "end_epoch": end,
    "window_seconds": max(0, end - start),
    "query_errors": 0,
}
for key, (query, reducer) in queries.items():
    params = urllib.parse.urlencode(
        {"query": query, "start": start, "end": end, "step": 30}
    )
    try:
        with urllib.request.urlopen(
            base + "/api/v1/query_range?" + params,
            timeout=10,
        ) as response:
            payload = json.load(response)
        if payload.get("status") != "success":
            raise RuntimeError("query failed")
        values = []
        for series in payload["data"]["result"]:
            for _, raw in series.get("values", []):
                value = float(raw)
                if math.isfinite(value):
                    values.append(value)
        if not values:
            raise RuntimeError("no samples")
        result[key] = round(reducer(values), 6)
    except Exception:
        result[key] = None
        result["query_errors"] += 1

cpu = result["host_cpu_peak_ratio"]
iowait = result["host_iowait_peak_ratio"]
result["investigate_cpu"] = cpu is not None and cpu > 0.80
result["investigate_iowait"] = iowait is not None and iowait > 0.20
result["secrets_or_business_identifiers_in_report"] = False
print(json.dumps(result, indent=2, sort_keys=True))
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="/opt/world-of-seeds-v2")
    parser.add_argument("--runtime-revision")
    parser.add_argument("--tool-revision", required=True)
    parser.add_argument("--campaign", default=time.strftime("g34-%m%d%H%M", time.gmtime()))
    parser.add_argument("--env-file", default="/etc/world-of-seeds-v2/environment")
    parser.add_argument("--compose", default="deploy/compose.rise2.v2.yaml")
    return parser.parse_args()


def run(
    command: list[str], *, cwd: Path, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )


def run_stdout(command: list[str], *, cwd: Path) -> str:
    return run(command, cwd=cwd, capture=True).stdout.strip()


def write_private_json(path: Path, payload: Any) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"invalid JSON object: {path}")
    return value


def validate_prepare(report: dict[str, Any]) -> None:
    expected = {
        "schema": LOAD_SCHEMA,
        "phase": "prepare",
        "account_count": 100,
        "managed_torrent_count": 209,
        "request_count": 214,
        "eligible_torrent_count": 205,
        "cooldown_torrent_count": 2,
        "ready_torrent_count": 2,
        "backlog_gt_200": True,
        "external_tracker_requests": 0,
        "secrets_or_business_identifiers_in_report": False,
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise RuntimeError(f"prepare invariant failed: {key}")


def validate_load(report: dict[str, Any], slots: int) -> None:
    checks = (
        report.get("schema") == LOAD_SCHEMA,
        report.get("phase") == "load",
        report.get("status") == "passed",
        report.get("slots") == slots,
        report.get("warmup_seconds", 0) >= 300,
        report.get("measurement_seconds", 0) >= 1800,
        report.get("duration_seconds", 0) >= 2100,
        report.get("famine_count") == 0,
        report.get("duplicate_count") == 0,
        report.get("corruption_count") == 0,
        report.get("unexpected_transition_count") == 0,
        report.get("scheduler_cycle_p95_seconds", float("inf"))
        < report.get("scheduler_interval_seconds", 0),
        report.get("account_count") == 100,
        report.get("backlog_count") == 205,
        report.get("control_window_max") == 200,
        report.get("secrets_or_business_identifiers_in_report") is False,
    )
    if not all(checks):
        raise RuntimeError(f"load gate invariants failed for slots={slots}")


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.repo = Path(args.repo).resolve()
        self.env_file = Path(args.env_file).resolve()
        self.compose = args.compose
        self.tool_revision = args.tool_revision
        self.campaign = args.campaign
        self.script_dir = Path(__file__).resolve().parent
        self.load_helper = self.script_dir / "rise2_v2_scheduler_load.py"
        self.load_runtime = self.script_dir / "rise2_v2_scheduler_load_runtime.py"
        self.runner_path = Path(__file__).resolve()
        self.runtime_revision = args.runtime_revision or self.git("rev-parse", "HEAD")
        self.pilot_root = Path("/var/lib/world-of-seeds-v2/pilot") / self.runtime_revision
        self.ledger = self.pilot_root / "ledger.json"
        self.evidence_root = self.pilot_root / f"load-{self.campaign}"
        self.stack_stopped = False

    def git(self, *args: str) -> str:
        return run_stdout(["git", *args], cwd=self.repo)

    def dc(self, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
        return run(
            [
                "docker",
                "compose",
                "--env-file",
                str(self.env_file),
                "-f",
                self.compose,
                *args,
            ],
            cwd=self.repo,
            capture=capture,
        )

    def verify_tool_blob(self, local_path: Path, repo_path: str) -> None:
        if not local_path.is_file() or local_path.is_symlink():
            raise RuntimeError(f"tool missing or symlinked: {local_path}")
        expected = self.git("rev-parse", f"{self.tool_revision}:{repo_path}")
        actual = self.git("hash-object", str(local_path))
        if actual != expected:
            raise RuntimeError(f"tool blob mismatch: {repo_path}")

    def run_tool(self, *args: str) -> dict[str, Any]:
        command = [
            "docker",
            "compose",
            "--env-file",
            str(self.env_file),
            "-f",
            self.compose,
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "WOS_RISE2_PILOT_ACK=V2-33",
            "-v",
            f"{self.load_helper}:/bootstrap/rise2_v2_scheduler_load.py:ro",
            "-v",
            f"{self.load_runtime}:/bootstrap/rise2_v2_scheduler_load_runtime.py:ro",
            "scheduler",
            "python",
            "-c",
            IN_CONTAINER_LAUNCHER,
            *args,
        ]
        completed = subprocess.run(
            command,
            cwd=self.repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise RuntimeError("load helper returned invalid JSON")
        return value

    def collect_prometheus(self, start: int, end: int) -> dict[str, Any]:
        output = self.dc(
            "run",
            "--rm",
            "--no-deps",
            "scheduler",
            "python",
            "-c",
            PROMETHEUS_COLLECTOR,
            str(start),
            str(end),
            capture=True,
        ).stdout
        value = json.loads(output)
        if not isinstance(value, dict) or value.get("schema") != PROM_SCHEMA:
            raise RuntimeError("invalid Prometheus aggregate")
        return value

    def stop_control_plane(self) -> None:
        self.dc("stop", "worker", "scheduler")
        self.stack_stopped = True
        running = self.dc(
            "ps", "--services", "--status", "running", capture=True
        ).stdout.splitlines()
        if "worker" in running or "scheduler" in running:
            raise RuntimeError("worker or scheduler still running")

    def restore_control_plane(self) -> None:
        if self.stack_stopped:
            print("\n========== RECOVERY STACK ==========")
            self.dc("start", "worker", "scheduler")
            self.stack_stopped = False

    def precheck(self) -> None:
        if os.geteuid() != 0:
            raise RuntimeError("run this runner as root")
        if not re.fullmatch(r"[0-9a-f]{40}", self.tool_revision):
            raise RuntimeError("tool revision must be a full SHA")
        if not re.fullmatch(r"[0-9a-f]{40}", self.runtime_revision):
            raise RuntimeError("runtime revision must be a full SHA")
        if CAMPAIGN_RE.fullmatch(self.campaign) is None:
            raise RuntimeError("invalid campaign id")
        if self.git("rev-parse", "HEAD") != self.runtime_revision:
            raise RuntimeError("checkout revision mismatch")
        if self.git("status", "--porcelain"):
            raise RuntimeError("runtime checkout is dirty")
        if not self.ledger.is_file() or self.ledger.is_symlink():
            raise RuntimeError("pilot ledger missing or symlinked")
        if self.evidence_root.exists():
            raise RuntimeError(f"evidence root already exists: {self.evidence_root}")

        self.verify_tool_blob(self.load_helper, "scripts/rise2_v2_scheduler_load.py")
        self.verify_tool_blob(self.load_runtime, "scripts/rise2_v2_scheduler_load_runtime.py")
        self.verify_tool_blob(self.runner_path, "scripts/rise2_v2_run_load_gates.py")

        ledger = read_json(self.ledger)
        if ledger.get("revision") != self.runtime_revision or ledger.get("decision") is not None:
            raise RuntimeError("pilot ledger runtime/decision mismatch")
        checks = ledger.get("checks", {})
        if checks.get("preflight", {}).get("status") != "passed":
            raise RuntimeError("preflight gate is not passed")
        if checks.get("backup_restore", {}).get("status") != "passed":
            raise RuntimeError("backup_restore gate is not passed")
        if "load_1_slot" in checks or "load_2_slots" in checks:
            raise RuntimeError("load gates are already recorded")

        api_cid = self.dc("ps", "-q", "api", capture=True).stdout.strip()
        if not api_cid:
            raise RuntimeError("API container unavailable")
        api_revision = run_stdout(
            [
                "docker",
                "inspect",
                "-f",
                '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
                api_cid,
            ],
            cwd=self.repo,
        )
        if api_revision != self.runtime_revision:
            raise RuntimeError("runtime OCI revision mismatch")

    def prepare(self) -> None:
        status = self.run_tool("status", "--campaign", self.campaign)
        if any(
            status.get(key) != 0
            for key in ("account_count", "managed_torrent_count", "request_count")
        ):
            raise RuntimeError("campaign already exists")
        self.evidence_root.mkdir(mode=0o700, parents=False)
        report = self.run_tool("prepare", "--campaign", self.campaign)
        validate_prepare(report)
        write_private_json(self.evidence_root / "prepare.json", report)

    def run_gate(self, slots: int, stem: str) -> tuple[dict[str, Any], dict[str, Any], int, int]:
        print(f"\n========== LOAD {slots} SLOT(S) ==========")
        print("warmup=300s measurement=1800s")
        start = int(time.time())
        report = self.run_tool(
            "run",
            "--campaign",
            self.campaign,
            "--slots",
            str(slots),
            "--warmup-seconds",
            "300",
            "--measurement-seconds",
            "1800",
        )
        end = int(time.time())
        validate_load(report, slots)
        prom = self.collect_prometheus(start, end)
        report_path = self.evidence_root / f"{stem}.json"
        prom_path = self.evidence_root / f"{stem}.prometheus.json"
        aggregate_path = self.evidence_root / f"{stem}.aggregate.json"
        write_private_json(report_path, report)
        write_private_json(prom_path, prom)
        write_private_json(aggregate_path, {"load": report, "prometheus": prom})
        return report, prom, start, end

    def execute(self) -> None:
        print("========== V2-33 LOAD RUNNER ==========")
        print(f"runtime_revision={self.runtime_revision}")
        print(f"tool_revision={self.tool_revision}")
        print(f"campaign={self.campaign}")
        print("\n========== 1. PROVENANCE ==========")
        self.precheck()
        print("provenance=PASSED")

        print("\n========== 2. CONTROLLED WINDOW ==========")
        self.stop_control_plane()
        print("workers=STOPPED scheduler=STOPPED")
        self.prepare()
        print("campaign_prepare=PASSED")

        g3, p3, g3_start, g3_end = self.run_gate(1, "load_1_slot")
        print("load_1_slot=PASSED")
        g4, p4, g4_start, g4_end = self.run_gate(2, "load_2_slots")
        print("load_2_slots=PASSED")

        write_private_json(
            self.evidence_root / "timings.json",
            {
                "load_1_slot": {"start_epoch": g3_start, "end_epoch": g3_end},
                "load_2_slots": {"start_epoch": g4_start, "end_epoch": g4_end},
            },
        )

        print("\n========== 3. RESTORE NORMAL STACK ==========")
        self.restore_control_plane()
        time.sleep(5)
        self.dc("ps", "worker", "scheduler")

        summary = {
            "schema": SUMMARY_SCHEMA,
            "campaign": self.campaign,
            "load_1_slot": self._summary_gate(g3, p3),
            "load_2_slots": self._summary_gate(g4, p4),
            "secrets_or_business_identifiers_in_report": False,
        }
        write_private_json(self.evidence_root / "result.json", summary)
        print(json.dumps(summary, indent=2, sort_keys=True))

        print("\n========== EVIDENCE SHA256 ==========")
        for name in (
            "load_1_slot.aggregate.json",
            "load_2_slots.aggregate.json",
            "timings.json",
            "result.json",
        ):
            path = self.evidence_root / name
            print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {name}")
        print("\nV2-33 LOAD RUNNER: PASSED")
        print("ledger_recorded=false")
        print(f"evidence_root={self.evidence_root}")

    @staticmethod
    def _summary_gate(load: dict[str, Any], prom: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": load["status"],
            "duration_seconds": load["duration_seconds"],
            "scheduler_cycle_p95_seconds": load["scheduler_cycle_p95_seconds"],
            "scheduler_interval_seconds": load["scheduler_interval_seconds"],
            "famine_count": load["famine_count"],
            "duplicate_count": load["duplicate_count"],
            "corruption_count": load["corruption_count"],
            "unexpected_transition_count": load["unexpected_transition_count"],
            "prometheus_query_errors": prom["query_errors"],
            "investigate_cpu": prom["investigate_cpu"],
            "investigate_iowait": prom["investigate_iowait"],
        }


def main() -> int:
    args = parse_args()
    runner = Runner(args)
    try:
        runner.execute()
    except (
        subprocess.CalledProcessError,
        RuntimeError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"\nV2-33 LOAD RUNNER: FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            runner.restore_control_plane()
        except Exception as exc:  # recovery must not hide the original failure
            print(f"RECOVERY ERROR: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
