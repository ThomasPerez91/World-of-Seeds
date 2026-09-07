#!/usr/bin/env python3
"""Run and preserve V2-33 Gate 6 transfer/manifest evidence on Rise2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUNNER_REPO_PATH = "scripts/rise2_v2_run_transfer_manifest_gate.py"
PROBE_REPO_PATH = "scripts/rise2_v2_transfer_manifest_probe.py"
EVIDENCE_SCHEMA = "world-of-seeds-v2-rise2-transfer-manifest/v1"
PRIOR_GATES = (
    "preflight",
    "backup_restore",
    "load_1_slot",
    "load_2_slots",
    "websocket_recovery",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="/opt/world-of-seeds-v2")
    parser.add_argument("--runtime-revision", required=True)
    parser.add_argument("--tool-revision", required=True)
    parser.add_argument("--campaign", default=time.strftime("tm-%m%d%H%M", time.gmtime()))
    parser.add_argument("--env-file", default="/etc/world-of-seeds-v2/environment")
    parser.add_argument("--compose", default="deploy/compose.rise2.v2.yaml")
    return parser.parse_args()


def run(
    command: list[str],
    *,
    cwd: Path,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture,
    )


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.repo = Path(args.repo).resolve()
        self.env_file = Path(args.env_file).resolve()
        self.compose = args.compose
        self.runtime_revision = args.runtime_revision
        self.tool_revision = args.tool_revision
        self.campaign = args.campaign
        self.script_dir = Path(__file__).resolve().parent
        self.runner_path = Path(__file__).resolve()
        self.probe_path = self.script_dir / "rise2_v2_transfer_manifest_probe.py"
        self.pilot_root = Path("/var/lib/world-of-seeds-v2/pilot") / self.runtime_revision
        self.ledger = self.pilot_root / "ledger.json"
        self.evidence_root = self.pilot_root / f"transfer-manifest-{self.campaign}"
        self.prechecked = False

    def git(self, *args: str) -> str:
        return run(["git", *args], cwd=self.repo, capture=True).stdout.strip()

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

    def inspect(self, cid: str, template: str) -> str:
        return run(
            ["docker", "inspect", "-f", template, cid],
            cwd=self.repo,
            capture=True,
        ).stdout.strip()

    def service_ids(self, service: str) -> list[str]:
        output = self.dc("ps", "-q", service, capture=True).stdout
        return [line for line in output.splitlines() if line]

    def verify_blob(self, path: Path, repo_path: str) -> None:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"tool missing or symlinked: {repo_path}")
        expected = self.git("rev-parse", f"{self.tool_revision}:{repo_path}")
        actual = self.git("hash-object", str(path))
        if actual != expected:
            raise RuntimeError(f"tool blob mismatch: {repo_path}")

    def verify_app_runtime(self) -> None:
        ledger = json.loads(self.ledger.read_text(encoding="utf-8"))
        digest = ledger["image_digest"]
        ids = (
            self.service_ids("api")
            + self.service_ids("worker")
            + self.service_ids("scheduler")
        )
        if len(set(ids)) != 4:
            raise RuntimeError("expected API, two workers and one scheduler")
        for cid in set(ids):
            if self.inspect(cid, "{{.State.Running}}") != "true":
                raise RuntimeError("application control-plane container is not running")
            image = self.inspect(cid, "{{.Config.Image}}")
            revision = self.inspect(
                cid,
                '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
            )
            if not image.endswith(f"@{digest}") or revision != self.runtime_revision:
                raise RuntimeError("application control-plane provenance mismatch")

    def precheck(self) -> None:
        if os.geteuid() != 0:
            raise RuntimeError("run this runner as root")
        if SHA_RE.fullmatch(self.runtime_revision) is None:
            raise RuntimeError("runtime revision must be a full SHA")
        if SHA_RE.fullmatch(self.tool_revision) is None:
            raise RuntimeError("tool revision must be a full SHA")
        if CAMPAIGN_RE.fullmatch(self.campaign) is None:
            raise RuntimeError("invalid campaign id")
        if self.git("rev-parse", "HEAD") != self.runtime_revision:
            raise RuntimeError("checkout revision mismatch")
        if self.git("status", "--porcelain"):
            raise RuntimeError("runtime checkout is dirty")
        if not self.ledger.is_file() or self.ledger.is_symlink():
            raise RuntimeError("pilot ledger missing or symlinked")
        if stat.S_IMODE(self.ledger.stat().st_mode) != 0o600:
            raise RuntimeError("pilot ledger mode must be 0600")
        if self.evidence_root.exists():
            raise RuntimeError("gate6 campaign evidence path already exists")

        self.verify_blob(self.runner_path, RUNNER_REPO_PATH)
        self.verify_blob(self.probe_path, PROBE_REPO_PATH)

        ledger = json.loads(self.ledger.read_text(encoding="utf-8"))
        if ledger.get("revision") != self.runtime_revision or ledger.get("decision") is not None:
            raise RuntimeError("pilot ledger runtime/decision mismatch")
        checks = ledger.get("checks", {})
        for name in PRIOR_GATES:
            if checks.get(name, {}).get("status") != "passed":
                raise RuntimeError(f"{name} gate is not passed")
        if "transfer_manifest" in checks:
            raise RuntimeError("transfer_manifest is already recorded")

        self.verify_app_runtime()
        self.prechecked = True

    def probe_command(self, mode: str) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            str(self.env_file),
            "-f",
            self.compose,
            "run",
            "--rm",
            "--no-deps",
            "-v",
            f"{self.probe_path}:/bootstrap/rise2_v2_transfer_manifest_probe.py:ro",
            "scheduler",
            "python",
            "/bootstrap/rise2_v2_transfer_manifest_probe.py",
            mode,
            "--campaign",
            self.campaign,
        ]

    def run_probe(self, mode: str, *, capture: bool = False) -> subprocess.CompletedProcess[str]:
        completed = run(
            self.probe_command(mode),
            cwd=self.repo,
            capture=capture,
            check=False,
        )
        if completed.returncode != 0:
            if capture and completed.stderr:
                print(completed.stderr[-4000:], file=sys.stderr, end="")
            raise RuntimeError(f"gate6 probe mode {mode} failed")
        return completed

    @staticmethod
    def parse_result(stdout: str) -> dict[str, Any]:
        lines = [line for line in stdout.splitlines() if line.strip()]
        if not lines:
            raise RuntimeError("gate6 probe produced no aggregate result")
        try:
            value = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise RuntimeError("gate6 probe aggregate result is invalid") from exc
        if not isinstance(value, dict):
            raise RuntimeError("gate6 probe aggregate result must be an object")
        return value

    @staticmethod
    def validate_result(result: dict[str, Any]) -> None:
        expected_keys = {
            "manifest_file_count",
            "manifest_pages",
            "integrity_failures",
            "residual_leases",
            "limit_violations",
            "progressive_start",
            "pause_resume_cancel_verified",
            "secrets_or_business_identifiers_in_report",
        }
        if set(result) != expected_keys:
            raise RuntimeError("gate6 probe aggregate keys are incomplete")
        if result["manifest_file_count"] < 50_000:
            raise RuntimeError("gate6 manifest proof is below 50000 files")
        if result["manifest_pages"] < 100:
            raise RuntimeError("gate6 did not traverse the full paginated manifest")
        for key in ("integrity_failures", "residual_leases", "limit_violations"):
            if result[key] != 0:
                raise RuntimeError(f"gate6 requires {key}=0")
        if result["progressive_start"] is not True:
            raise RuntimeError("gate6 progressive transfer proof failed")
        if result["pause_resume_cancel_verified"] is not True:
            raise RuntimeError("gate6 pause/resume/cancel proof failed")
        if result["secrets_or_business_identifiers_in_report"] is not False:
            raise RuntimeError("gate6 aggregate evidence is not secret-safe")

    def execute(self) -> None:
        print("========== V2-33 TRANSFER / MANIFEST GATE ==========")
        print(f"runtime_revision={self.runtime_revision}")
        print(f"tool_revision={self.tool_revision}")
        print(f"campaign={self.campaign}")
        self.precheck()
        print("provenance=PASSED prior_gates=5/12")

        self.evidence_root.mkdir(mode=0o700)
        result: dict[str, Any] | None = None
        try:
            completed = self.run_probe("run", capture=True)
            result = self.parse_result(completed.stdout)
            self.validate_result(result)
        finally:
            if self.prechecked:
                try:
                    self.run_probe("cleanup", capture=True)
                except Exception as exc:
                    print(f"RECOVERY CLEANUP ERROR: {exc}", file=sys.stderr)
                    if result is not None:
                        raise

        assert result is not None
        evidence = {"schema": EVIDENCE_SCHEMA, **result}
        evidence_path = self.evidence_root / "transfer_manifest.aggregate.json"
        write_private_json(evidence_path, evidence)
        digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()

        print(json.dumps(evidence, indent=2, sort_keys=True))
        print(f"evidence_sha256={digest}")
        print("V2-33 TRANSFER / MANIFEST GATE: PASSED")
        print("ledger_recorded=false")
        print(f"evidence_root={self.evidence_root}")


def main() -> int:
    args = parse_args()
    runner = Runner(args)
    try:
        runner.execute()
    except Exception as exc:
        print(f"V2-33 TRANSFER / MANIFEST GATE: FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
