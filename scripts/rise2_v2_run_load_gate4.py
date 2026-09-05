#!/usr/bin/env python3
"""Resume only V2-33 load gate 4 while preserving failed evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BASE_PATH = Path(__file__).resolve().with_name("rise2_v2_run_load_gates.py")
SPEC = importlib.util.spec_from_file_location("rise2_v2_load_runner_base", BASE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load V2-33 load runner base")
base = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(base)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="/opt/world-of-seeds-v2")
    parser.add_argument("--runtime-revision")
    parser.add_argument("--tool-revision", required=True)
    parser.add_argument("--campaign", default=time.strftime("g4-%m%d%H%M", time.gmtime()))
    parser.add_argument("--env-file", default="/etc/world-of-seeds-v2/environment")
    parser.add_argument("--compose", default="deploy/compose.rise2.v2.yaml")
    return parser.parse_args()


class Gate4Runner(base.Runner):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__(args)
        self.resume_runner_path = Path(__file__).resolve()

    def verify_resume_tool(self) -> None:
        if not self.resume_runner_path.is_file() or self.resume_runner_path.is_symlink():
            raise RuntimeError("gate4 resume tool missing or symlinked")
        expected = self.git(
            "rev-parse",
            f"{self.tool_revision}:scripts/rise2_v2_run_load_gate4.py",
        )
        actual = self.git("hash-object", str(self.resume_runner_path))
        if actual != expected:
            raise RuntimeError("gate4 resume tool blob mismatch")

    def precheck(self) -> None:
        if os.geteuid() != 0:
            raise RuntimeError("run this runner as root")
        if not re.fullmatch(r"[0-9a-f]{40}", self.tool_revision):
            raise RuntimeError("tool revision must be a full SHA")
        if not re.fullmatch(r"[0-9a-f]{40}", self.runtime_revision):
            raise RuntimeError("runtime revision must be a full SHA")
        if base.CAMPAIGN_RE.fullmatch(self.campaign) is None:
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
        self.verify_resume_tool()

        ledger = base.read_json(self.ledger)
        if ledger.get("revision") != self.runtime_revision or ledger.get("decision") is not None:
            raise RuntimeError("pilot ledger runtime/decision mismatch")
        checks = ledger.get("checks", {})
        for name in ("preflight", "backup_restore", "load_1_slot"):
            if checks.get(name, {}).get("status") != "passed":
                raise RuntimeError(f"{name} gate is not passed")
        if "load_2_slots" in checks:
            raise RuntimeError("load_2_slots gate is already recorded")

        api_cid = self.dc("ps", "-q", "api", capture=True).stdout.strip()
        if not api_cid:
            raise RuntimeError("API container unavailable")
        api_revision = base.run_stdout(
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
            base.IN_CONTAINER_LAUNCHER,
            *args,
        ]
        completed = subprocess.run(
            command,
            cwd=self.repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
        )
        if completed.returncode not in (0, 2):
            raise subprocess.CalledProcessError(
                completed.returncode,
                command,
                output=completed.stdout,
            )
        value = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise RuntimeError("load helper returned invalid JSON")
        failed = value.get("status") == "failed"
        if (completed.returncode == 2) != failed:
            raise RuntimeError("load helper exit status/report mismatch")
        return value

    def run_gate(
        self,
        slots: int,
        stem: str,
    ) -> tuple[dict[str, Any], dict[str, Any], int, int]:
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
        prom = self.collect_prometheus(start, end)
        report_path = self.evidence_root / f"{stem}.json"
        prom_path = self.evidence_root / f"{stem}.prometheus.json"
        aggregate_path = self.evidence_root / f"{stem}.aggregate.json"
        base.write_private_json(report_path, report)
        base.write_private_json(prom_path, prom)
        base.write_private_json(aggregate_path, {"load": report, "prometheus": prom})
        print(json.dumps(base.Runner._summary_gate(report, prom), indent=2, sort_keys=True))
        base.validate_load(report, slots)
        return report, prom, start, end

    def execute(self) -> None:
        print("========== V2-33 LOAD GATE4 RESUME ==========")
        print(f"runtime_revision={self.runtime_revision}")
        print(f"tool_revision={self.tool_revision}")
        print(f"campaign={self.campaign}")
        self.precheck()
        print("provenance=PASSED load_1_slot=RECORDED")

        self.stop_control_plane()
        print("workers=STOPPED scheduler=STOPPED")
        self.prepare()
        print("campaign_prepare=PASSED")

        report, prom, _, _ = self.run_gate(2, "load_2_slots")
        print("load_2_slots=PASSED")

        print("\n========== RESTORE NORMAL STACK ==========")
        self.restore_control_plane()
        time.sleep(5)
        self.dc("ps", "worker", "scheduler")

        result = {
            "schema": base.SUMMARY_SCHEMA,
            "campaign": self.campaign,
            "load_2_slots": base.Runner._summary_gate(report, prom),
            "secrets_or_business_identifiers_in_report": False,
        }
        base.write_private_json(self.evidence_root / "result.json", result)
        print(json.dumps(result, indent=2, sort_keys=True))
        print("\nV2-33 LOAD GATE4 RESUME: PASSED")
        print("ledger_recorded=false")
        print(f"evidence_root={self.evidence_root}")


def main() -> int:
    args = parse_args()
    runner = Gate4Runner(args)
    try:
        runner.execute()
    except (
        subprocess.CalledProcessError,
        RuntimeError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"\nV2-33 LOAD GATE4 RESUME: FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            runner.restore_control_plane()
        except Exception as exc:
            print(f"RECOVERY ERROR: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
