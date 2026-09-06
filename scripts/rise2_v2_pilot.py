#!/usr/bin/env python3
"""Create and validate a secret-free Rise2 V2 pilot acceptance ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "world-of-seeds-v2-rise2-pilot/v1"
PROJECT = "world-of-seeds-v2-rise2"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
METRIC_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
STATUSES = {"passed", "failed"}
DECISIONS = {"go", "go_limited", "no_go"}
CHECKS = (
    "preflight",
    "backup_restore",
    "load_1_slot",
    "load_2_slots",
    "websocket_recovery",
    "transfer_manifest",
    "dependency_failures",
    "resource_pressure",
    "security_observability",
    "test_data_cleanup",
    "pilot_accounts",
    "rollback",
)


class PilotLedgerError(RuntimeError):
    """A ledger invariant failed."""


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _regular_file(path: Path, description: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PilotLedgerError(f"{description} is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PilotLedgerError(f"{description} must be a regular file")
    return path


def _load(path: Path) -> dict[str, Any]:
    _regular_file(path, "pilot ledger")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PilotLedgerError("pilot ledger mode must be 0600")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotLedgerError("pilot ledger is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PilotLedgerError("pilot ledger must be a JSON object")
    return value


def _write(path: Path, value: Mapping[str, Any], *, create: bool = False) -> None:
    path = path.resolve(strict=False)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if create and path.exists():
        raise PilotLedgerError("pilot ledger already exists")
    if path.exists():
        _regular_file(path, "pilot ledger")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _evidence_sha256(path: Path) -> str:
    path = _regular_file(path, "evidence artifact")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_metric(raw: str) -> tuple[str, bool | int | float]:
    key, separator, value = raw.partition("=")
    if not separator or METRIC_RE.fullmatch(key) is None:
        raise PilotLedgerError("metrics must use safe key=value names")
    if value == "true":
        parsed: bool | int | float = True
    elif value == "false":
        parsed = False
    else:
        try:
            parsed = (
                float(value)
                if any(character in value for character in ".eE")
                else int(value)
            )
        except ValueError as exc:
            raise PilotLedgerError("metric values must be numbers or booleans") from exc
        if isinstance(parsed, float) and not math.isfinite(parsed):
            raise PilotLedgerError("metric values must be finite")
    return key, parsed


def _number(metrics: Mapping[str, Any], key: str) -> float:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PilotLedgerError(f"required metric is missing or non-numeric: {key}")
    return float(value)


def _zero(metrics: Mapping[str, Any], *keys: str) -> None:
    for key in keys:
        if _number(metrics, key) != 0:
            raise PilotLedgerError(f"passing check requires {key}=0")


def _true(metrics: Mapping[str, Any], *keys: str) -> None:
    for key in keys:
        if metrics.get(key) is not True:
            raise PilotLedgerError(f"passing check requires {key}=true")


def _validate_passing_check(name: str, entry: Mapping[str, Any]) -> None:
    metrics = entry["metrics"]
    duration = float(entry["duration_seconds"])
    if name == "preflight":
        _true(metrics, "newgreedy_readable", "isolated_v2_storage")
        _zero(metrics, "policy_failures", "v1_mounts", "public_internal_ports")
    elif name == "backup_restore":
        _true(metrics, "postgres_restored", "content_canary_verified")
        _zero(metrics, "restore_failures", "secret_findings", "existing_target_writes")
        if duration > _number(metrics, "rto_seconds"):
            raise PilotLedgerError("restore duration exceeds the recorded RTO")
    elif name in {"load_1_slot", "load_2_slots"}:
        expected_slots = 1 if name == "load_1_slot" else 2
        if _number(metrics, "slots") != expected_slots:
            raise PilotLedgerError(f"{name} must record slots={expected_slots}")
        if _number(metrics, "warmup_seconds") < 300:
            raise PilotLedgerError("load warmup must last at least 300 seconds")
        if _number(metrics, "measurement_seconds") < 1800 or duration < 2100:
            raise PilotLedgerError(
                "load measurement must include 5 minutes warmup and 30 minutes"
            )
        _zero(
            metrics,
            "famine_count",
            "duplicate_count",
            "corruption_count",
            "unexpected_transition_count",
        )
        if _number(metrics, "scheduler_cycle_p95_seconds") >= _number(
            metrics, "scheduler_interval_seconds"
        ):
            raise PilotLedgerError(
                "scheduler p95 must remain below its configured interval"
            )
    elif name == "websocket_recovery":
        if (
            _number(metrics, "connections") < 100
            or _number(metrics, "reconnections") < 25
        ):
            raise PilotLedgerError(
                "WebSocket proof must cover 100 connections and 25 reconnects"
            )
        _zero(
            metrics, "idle_transactions", "resync_failures", "lost_events_after_resync"
        )
        _true(metrics, "memory_returned_to_plateau")
    elif name == "transfer_manifest":
        if _number(metrics, "manifest_file_count") < 50_000:
            raise PilotLedgerError("manifest proof must cover at least 50000 files")
        _zero(metrics, "integrity_failures", "residual_leases", "limit_violations")
        _true(metrics, "progressive_start", "pause_resume_cancel_verified")
    elif name == "dependency_failures":
        if _number(metrics, "scenarios") < 8:
            raise PilotLedgerError(
                "dependency proof must cover all eight failure families"
            )
        _zero(metrics, "false_successes", "lost_jobs", "recovery_failures")
        _true(metrics, "idempotent_recovery")
    elif name == "resource_pressure":
        _zero(metrics, "admission_failures", "unexplained_threshold_breaches")
        _true(metrics, "cpu_ram_io_disk_observed", "disk_pressure_fail_closed")
    elif name == "security_observability":
        _zero(
            metrics,
            "fixable_high_critical",
            "secret_findings",
            "business_identifier_findings",
        )
        _true(metrics, "public_metrics_blocked", "private_metrics_available")
    elif name == "test_data_cleanup":
        _zero(
            metrics,
            "remaining_test_users",
            "remaining_test_torrents",
            "remaining_test_files",
        )
        _true(metrics, "v1_unchanged")
    elif name == "pilot_accounts":
        if _number(metrics, "pilot_account_count") < 1:
            raise PilotLedgerError("at least one pilot account must be created")
        _zero(metrics, "v1_data_moves", "credentials_in_output")
        _true(metrics, "forced_credential_change", "v1_unchanged")
    elif name == "rollback":
        if duration > _number(metrics, "rto_seconds"):
            raise PilotLedgerError("rollback duration exceeds the recorded RTO")
        _zero(metrics, "health_failures", "authentication_failures", "v1_writes")
        _true(metrics, "v1_available", "v2_admission_suspended", "v2_volumes_preserved")


def validate(report: Mapping[str, Any], *, require_final: bool = False) -> None:
    if set(report) != {
        "schema",
        "project",
        "revision",
        "image_digest",
        "created_at",
        "checks",
        "decision",
    }:
        raise PilotLedgerError("pilot ledger has unexpected top-level fields")
    if report["schema"] != SCHEMA or report["project"] != PROJECT:
        raise PilotLedgerError("pilot ledger schema or project is invalid")
    if (
        not isinstance(report["revision"], str)
        or SHA_RE.fullmatch(report["revision"]) is None
    ):
        raise PilotLedgerError("revision must be a full lowercase commit SHA")
    if (
        not isinstance(report["image_digest"], str)
        or DIGEST_RE.fullmatch(report["image_digest"]) is None
    ):
        raise PilotLedgerError("image digest must be immutable")
    if not isinstance(report["created_at"], str):
        raise PilotLedgerError("created_at is invalid")
    checks = report["checks"]
    if not isinstance(checks, dict) or not set(checks).issubset(CHECKS):
        raise PilotLedgerError("pilot ledger contains unknown checks")
    for name, entry in checks.items():
        if not isinstance(entry, dict) or set(entry) != {
            "status",
            "duration_seconds",
            "metrics",
            "evidence_sha256",
            "recorded_at",
        }:
            raise PilotLedgerError(f"check {name} has invalid fields")
        if entry["status"] not in STATUSES:
            raise PilotLedgerError(f"check {name} has invalid status")
        duration = entry["duration_seconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration < 0
        ):
            raise PilotLedgerError(f"check {name} has invalid duration")
        metrics = entry["metrics"]
        if not isinstance(metrics, dict) or any(
            METRIC_RE.fullmatch(key) is None for key in metrics
        ):
            raise PilotLedgerError(f"check {name} has invalid metric names")
        for value in metrics.values():
            if isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise PilotLedgerError(f"check {name} has a non-aggregate metric")
        if (
            not isinstance(entry["evidence_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", entry["evidence_sha256"]) is None
        ):
            raise PilotLedgerError(f"check {name} has invalid evidence hash")
        if not isinstance(entry["recorded_at"], str):
            raise PilotLedgerError(f"check {name} has invalid timestamp")
        if entry["status"] == "passed":
            _validate_passing_check(name, entry)
    decision = report["decision"]
    if decision is not None:
        if not isinstance(decision, dict) or set(decision) != {
            "status",
            "approval_ref",
            "recorded_at",
        }:
            raise PilotLedgerError("pilot decision has invalid fields")
        if decision["status"] not in DECISIONS:
            raise PilotLedgerError("pilot decision is invalid")
        if (
            not isinstance(decision["approval_ref"], str)
            or REFERENCE_RE.fullmatch(decision["approval_ref"]) is None
        ):
            raise PilotLedgerError("approval reference is invalid")
        if not isinstance(decision["recorded_at"], str):
            raise PilotLedgerError("pilot decision timestamp is invalid")
    if require_final:
        missing = set(CHECKS) - set(checks)
        if missing:
            raise PilotLedgerError(
                f"pilot ledger is missing checks: {', '.join(sorted(missing))}"
            )
        if decision is None:
            raise PilotLedgerError("pilot ledger has no decision")
        failed = {name for name, entry in checks.items() if entry["status"] != "passed"}
        if decision["status"] in {"go", "go_limited"} and failed:
            raise PilotLedgerError("a go decision cannot contain failed checks")
        if decision["status"] == "no_go" and not failed:
            raise PilotLedgerError(
                "a no-go decision must identify at least one failed check"
            )


def initialize(path: Path, revision: str, image_digest: str) -> None:
    report = {
        "schema": SCHEMA,
        "project": PROJECT,
        "revision": revision,
        "image_digest": image_digest,
        "created_at": _timestamp(),
        "checks": {},
        "decision": None,
    }
    validate(report)
    _write(path, report, create=True)


def record(
    path: Path,
    name: str,
    status_value: str,
    duration_seconds: float,
    evidence: Path,
    raw_metrics: list[str],
) -> None:
    report = _load(path)
    validate(report)
    if report["decision"] is not None:
        raise PilotLedgerError("a finalized pilot ledger cannot be changed")
    metrics = dict(_parse_metric(raw) for raw in raw_metrics)
    report["checks"][name] = {
        "status": status_value,
        "duration_seconds": duration_seconds,
        "metrics": metrics,
        "evidence_sha256": _evidence_sha256(evidence),
        "recorded_at": _timestamp(),
    }
    validate(report)
    _write(path, report)


def finalize(path: Path, decision: str, approval_ref: str) -> None:
    report = _load(path)
    validate(report)
    if report["decision"] is not None:
        raise PilotLedgerError("pilot ledger is already finalized")
    report["decision"] = {
        "status": decision,
        "approval_ref": approval_ref,
        "recorded_at": _timestamp(),
    }
    validate(report, require_final=True)
    _write(path, report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("report", type=Path)
    init_parser.add_argument("--revision", required=True)
    init_parser.add_argument("--image-digest", required=True)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("report", type=Path)
    record_parser.add_argument("check", choices=CHECKS)
    record_parser.add_argument("--status", choices=sorted(STATUSES), required=True)
    record_parser.add_argument("--duration-seconds", type=float, required=True)
    record_parser.add_argument("--evidence", type=Path, required=True)
    record_parser.add_argument("--metric", action="append", default=[])

    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("report", type=Path)
    finalize_parser.add_argument("--decision", choices=sorted(DECISIONS), required=True)
    finalize_parser.add_argument("--approval-ref", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("report", type=Path)
    validate_parser.add_argument("--require-final", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "init":
            initialize(args.report, args.revision, args.image_digest)
        elif args.command == "record":
            record(
                args.report,
                args.check,
                args.status,
                args.duration_seconds,
                args.evidence,
                args.metric,
            )
        elif args.command == "finalize":
            finalize(args.report, args.decision, args.approval_ref)
        else:
            validate(_load(args.report), require_final=args.require_final)
    except PilotLedgerError as exc:
        print(f"Rise2 V2 pilot ledger error: {exc}", file=os.sys.stderr)
        return 1
    print("Rise2 V2 pilot ledger passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
