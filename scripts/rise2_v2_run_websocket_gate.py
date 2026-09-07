#!/usr/bin/env python3
"""Run and preserve V2-33 Gate 5 WebSocket recovery evidence on Rise2."""

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
RUNNER_REPO_PATH = "scripts/rise2_v2_run_websocket_gate.py"
PROBE_REPO_PATH = "scripts/rise2_v2_websocket_probe.py"
EVIDENCE_SCHEMA = "world-of-seeds-v2-rise2-websocket-recovery/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="/opt/world-of-seeds-v2")
    parser.add_argument("--runtime-revision", required=True)
    parser.add_argument("--tool-revision", required=True)
    parser.add_argument("--campaign", default=time.strftime("ws-%m%d%H%M", time.gmtime()))
    parser.add_argument("--env-file", default="/etc/world-of-seeds-v2/environment")
    parser.add_argument("--compose", default="deploy/compose.rise2.v2.yaml")
    return parser.parse_args()


def run(
    command: list[str],
    *,
    cwd: Path,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
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
        self.probe_path = self.script_dir / "rise2_v2_websocket_probe.py"
        self.pilot_root = Path("/var/lib/world-of-seeds-v2/pilot") / self.runtime_revision
        self.ledger = self.pilot_root / "ledger.json"
        self.evidence_root = self.pilot_root / f"websocket-{self.campaign}"
        self.state_dir = Path("/run") / f"wos-v233-{self.campaign}"
        self.secret_file = self.state_dir / "sessions.json"
        self.redis_stopped = False
        self.created_users = False
        self.prechecked = False
        self.baseline_process: subprocess.Popen[str] | None = None

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

    def api_id(self) -> str:
        ids = self.service_ids("api")
        if len(ids) != 1:
            raise RuntimeError("API container unavailable")
        return ids[0]

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

    def wait_healthy(self, service: str, timeout: int = 90) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ids = self.service_ids(service)
            if ids and all(
                self.inspect(cid, "{{.State.Health.Status}}") == "healthy" for cid in ids
            ):
                return
            time.sleep(2)
        raise RuntimeError(f"{service} did not become healthy")

    def memory_rss_bytes(self) -> int:
        pid = int(self.inspect(self.api_id(), "{{.State.Pid}}"))
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
        raise RuntimeError("unable to read API RSS")

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
            "--user",
            "0:0",
            "-v",
            f"{self.probe_path}:/bootstrap/rise2_v2_websocket_probe.py:ro",
            "-v",
            f"{self.state_dir}:/run/gate5",
            "scheduler",
            "python",
            "/bootstrap/rise2_v2_websocket_probe.py",
            mode,
            "--campaign",
            self.campaign,
        ]

    def run_probe(self, mode: str, *, capture: bool = False) -> subprocess.CompletedProcess[str]:
        return run(self.probe_command(mode), cwd=self.repo, capture=capture)

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
        if self.evidence_root.exists() or self.state_dir.exists():
            raise RuntimeError("gate5 campaign path already exists")

        self.verify_blob(self.runner_path, RUNNER_REPO_PATH)
        self.verify_blob(self.probe_path, PROBE_REPO_PATH)

        ledger = json.loads(self.ledger.read_text(encoding="utf-8"))
        if ledger.get("revision") != self.runtime_revision or ledger.get("decision") is not None:
            raise RuntimeError("pilot ledger runtime/decision mismatch")
        checks = ledger.get("checks", {})
        for name in ("preflight", "backup_restore", "load_1_slot", "load_2_slots"):
            if checks.get(name, {}).get("status") != "passed":
                raise RuntimeError(f"{name} gate is not passed")
        if "websocket_recovery" in checks:
            raise RuntimeError("websocket_recovery is already recorded")

        self.verify_app_runtime()
        self.prechecked = True

    def setup_users(self) -> None:
        self.state_dir.mkdir(mode=0o700)
        fd = os.open(self.secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.created_users = True
        self.run_probe("setup")
        if stat.S_IMODE(self.secret_file.stat().st_mode) != 0o600:
            raise RuntimeError("gate5 session file mode changed")

    def cleanup_users(self) -> None:
        if self.created_users:
            self.run_probe("cleanup")
            self.created_users = False

    def start_baseline(self) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            self.probe_command("baseline"),
            cwd=self.repo,
            text=True,
            stdout=subprocess.DEVNULL,
        )
        self.baseline_process = process
        return process

    def wait_file(self, path: Path, timeout: int = 120) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_file():
                return
            time.sleep(1)
        raise RuntimeError(f"timed out waiting for {path.name}")

    def recreate_api(self) -> None:
        self.dc("up", "-d", "--no-deps", "--force-recreate", "api")
        self.wait_healthy("api")
        self.verify_app_runtime()

    def execute(self) -> None:
        print("========== V2-33 WEBSOCKET RECOVERY GATE ==========")
        print(f"runtime_revision={self.runtime_revision}")
        print(f"tool_revision={self.tool_revision}")
        print(f"campaign={self.campaign}")
        self.precheck()
        print("provenance=PASSED prior_gates=4/12")

        self.evidence_root.mkdir(mode=0o700)
        self.setup_users()
        memory_before = self.memory_rss_bytes()

        baseline_process = self.start_baseline()
        self.wait_file(self.state_dir / "baseline.ready")
        print("connections=100 tiers=10/25/50/100")

        self.recreate_api()
        baseline_rc = baseline_process.wait(timeout=120)
        self.baseline_process = None
        if baseline_rc != 0:
            raise RuntimeError("baseline WebSocket probe failed")
        baseline = json.loads(
            (self.state_dir / "baseline.json").read_text(encoding="utf-8")
        )
        baseline_metrics = {
            "connections": baseline.get("connections"),
            "connection_tiers": baseline.get("connection_tiers"),
            "subscription_ready": baseline.get("subscription_ready"),
            "event_deliveries": baseline.get("event_deliveries"),
            "api_restart_disconnects": baseline.get("api_restart_disconnects"),
            "idle_transactions": baseline.get("idle_transactions"),
        }
        write_private_json(
            self.evidence_root / "websocket_recovery.baseline.json",
            baseline_metrics,
        )
        print(f"baseline_metrics={json.dumps(baseline_metrics, sort_keys=True)}")
        if (
            baseline.get("connections") != 100
            or baseline.get("connection_tiers") != [10, 25, 50, 100]
            or baseline.get("subscription_ready") != 100
            or baseline.get("event_deliveries") != 100
            or baseline.get("api_restart_disconnects") != 100
            or baseline.get("idle_transactions") != 0
        ):
            raise RuntimeError(
                "baseline WebSocket invariants failed: "
                f"{json.dumps(baseline_metrics, sort_keys=True)}"
            )
        print("subscription_ready=100 api_restart_disconnects=100 idle_transactions=0")

        reconnect = json.loads(self.run_probe("reconnect", capture=True).stdout)
        if reconnect != {"event_deliveries": 25, "reconnections": 25}:
            raise RuntimeError("reconnection proof failed")
        print("reconnections=25")

        self.dc("stop", "redis")
        self.redis_stopped = True
        down = json.loads(self.run_probe("redis-down", capture=True).stdout)
        if (
            down.get("resync_attempts") != 25
            or down.get("resync_successes") != 25
            or down.get("lost_event_publish_observed") is not True
        ):
            raise RuntimeError("Redis interruption proof failed")
        print("redis_interruption=resync_required lost_event=OBSERVED")

        self.dc("start", "redis")
        self.redis_stopped = False
        self.wait_healthy("redis")
        resync = json.loads(self.run_probe("resync-get", capture=True).stdout)
        if resync.get("authoritative_resync_successes") != 25:
            raise RuntimeError("authoritative GET resync failed")
        print("authoritative_resync=25/25")

        self.cleanup_users()
        time.sleep(10)
        memory_after = self.memory_rss_bytes()
        if memory_after > memory_before + 64 * 1024 * 1024:
            raise RuntimeError("API memory did not return to the allowed plateau")

        evidence = {
            "schema": EVIDENCE_SCHEMA,
            "connections": 100,
            "subscription_ready": 100,
            "reconnections": 25,
            "idle_transactions": 0,
            "resync_failures": 0,
            "lost_events_after_resync": 0,
            "memory_returned_to_plateau": True,
            "api_restart_disconnects": 100,
            "baseline_event_deliveries": 100,
            "reconnect_event_deliveries": 25,
            "memory_before_bytes": memory_before,
            "memory_after_bytes": memory_after,
            "secrets_or_business_identifiers_in_report": False,
        }
        evidence_path = self.evidence_root / "websocket_recovery.aggregate.json"
        write_private_json(evidence_path, evidence)
        digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()

        print(json.dumps(evidence, indent=2, sort_keys=True))
        print(f"evidence_sha256={digest}")
        print("V2-33 WEBSOCKET RECOVERY GATE: PASSED")
        print("ledger_recorded=false")
        print(f"evidence_root={self.evidence_root}")

    def recover(self) -> None:
        if self.baseline_process is not None and self.baseline_process.poll() is None:
            self.baseline_process.terminate()
            try:
                self.baseline_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.baseline_process.kill()
                self.baseline_process.wait(timeout=5)
        self.baseline_process = None
        if not self.prechecked:
            return
        if self.redis_stopped:
            try:
                self.dc("start", "redis")
                self.wait_healthy("redis")
            finally:
                self.redis_stopped = False
        try:
            self.recreate_api()
        except Exception as exc:
            print(f"RECOVERY API ERROR: {exc}", file=sys.stderr)
        try:
            self.cleanup_users()
        except Exception as exc:
            print(f"RECOVERY CLEANUP ERROR: {exc}", file=sys.stderr)
        try:
            if self.state_dir.exists():
                for child in self.state_dir.iterdir():
                    child.unlink(missing_ok=True)
                self.state_dir.rmdir()
        except OSError as exc:
            print(f"RECOVERY STATE ERROR: {exc}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    runner = Runner(args)
    try:
        runner.execute()
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        RuntimeError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        print(f"V2-33 WEBSOCKET RECOVERY GATE: FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        runner.recover()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
