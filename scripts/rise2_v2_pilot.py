#!/usr/bin/env python3
"""Create and validate a secret-free Rise2 V2 pilot acceptance ledger."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import socket
import stat
import subprocess
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = "world-of-seeds-v2-rise2-pilot/v1"
PROJECT = "world-of-seeds-v2-rise2"
PILOT_HOST = "rise2-01"
APPROVED_RTO_SECONDS = 14_400
DEFAULT_REPO = Path("/opt/world-of-seeds-v2")
DEFAULT_ENV_FILE = Path("/etc/world-of-seeds-v2/environment")
DEFAULT_COMPOSE = Path("deploy/compose.rise2.v2.yaml")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REFERENCE_RE = re.compile(
    r"^(?:ops-approval|v2-33-(?:go|go-limited|no-go))-[0-9]{8}$"
)
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
METRICS_BY_CHECK: dict[str, frozenset[str]] = {
    "preflight": frozenset(
        {
            "newgreedy_readable",
            "isolated_v2_storage",
            "policy_failures",
            "v1_mounts",
            "public_internal_ports",
        }
    ),
    "backup_restore": frozenset(
        {
            "postgres_restored",
            "content_canary_verified",
            "restore_failures",
            "secret_findings",
            "existing_target_writes",
            "rto_seconds",
        }
    ),
    "load_1_slot": frozenset(
        {
            "slots",
            "warmup_seconds",
            "measurement_seconds",
            "famine_count",
            "duplicate_count",
            "corruption_count",
            "unexpected_transition_count",
            "scheduler_cycle_p95_seconds",
            "scheduler_interval_seconds",
        }
    ),
    "load_2_slots": frozenset(
        {
            "slots",
            "warmup_seconds",
            "measurement_seconds",
            "famine_count",
            "duplicate_count",
            "corruption_count",
            "unexpected_transition_count",
            "scheduler_cycle_p95_seconds",
            "scheduler_interval_seconds",
        }
    ),
    "websocket_recovery": frozenset(
        {
            "connections",
            "reconnections",
            "idle_transactions",
            "resync_failures",
            "lost_events_after_resync",
            "memory_returned_to_plateau",
        }
    ),
    "transfer_manifest": frozenset(
        {
            "manifest_file_count",
            "integrity_failures",
            "residual_leases",
            "limit_violations",
            "progressive_start",
            "pause_resume_cancel_verified",
        }
    ),
    "dependency_failures": frozenset(
        {
            "scenarios",
            "false_successes",
            "lost_jobs",
            "recovery_failures",
            "idempotent_recovery",
        }
    ),
    "resource_pressure": frozenset(
        {
            "admission_failures",
            "unexplained_threshold_breaches",
            "cpu_ram_io_disk_observed",
            "disk_pressure_fail_closed",
        }
    ),
    "security_observability": frozenset(
        {
            "fixable_high_critical",
            "secret_findings",
            "business_identifier_findings",
            "public_metrics_blocked",
            "private_metrics_available",
        }
    ),
    "test_data_cleanup": frozenset(
        {
            "remaining_test_users",
            "remaining_test_torrents",
            "remaining_test_files",
            "v1_unchanged",
        }
    ),
    "pilot_accounts": frozenset(
        {
            "pilot_account_count",
            "v1_data_moves",
            "credentials_in_output",
            "forced_credential_change",
            "v1_unchanged",
        }
    ),
    "rollback": frozenset(
        {
            "rto_seconds",
            "health_failures",
            "authentication_failures",
            "v1_writes",
            "v1_available",
            "v2_admission_suspended",
            "v2_volumes_preserved",
        }
    ),
}
EVIDENCE_SCHEMAS = {
    "preflight": "world-of-seeds-v2-rise2-preflight/v1",
    "backup_restore": "world-of-seeds-v2-rise2-backup-restore/v1",
    "websocket_recovery": "world-of-seeds-v2-rise2-websocket-recovery/v1",
    "transfer_manifest": "world-of-seeds-v2-rise2-transfer-manifest/v1",
    "dependency_failures": "world-of-seeds-v2-rise2-dependency-failures/v1",
    "resource_pressure": "world-of-seeds-v2-rise2-resource-pressure/v1",
    "security_observability": "world-of-seeds-v2-rise2-security-observability/v1",
    "test_data_cleanup": "world-of-seeds-v2-rise2-test-data-cleanup/v1",
    "pilot_accounts": "world-of-seeds-v2-rise2-pilot-accounts/v1",
    "rollback": "world-of-seeds-v2-rise2-rollback/v1",
}
LOAD_EVIDENCE_SCHEMA = "world-of-seeds-v2-rise2-scheduler-load/v1"
MAX_EVIDENCE_BYTES = 2 * 1024 * 1024


class PilotLedgerError(RuntimeError):
    """A ledger invariant failed."""


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _timestamp_value(value: Any, description: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PilotLedgerError(f"{description} is invalid")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PilotLedgerError(f"{description} is invalid") from exc
    if parsed.tzinfo is None:
        raise PilotLedgerError(f"{description} is invalid")
    return parsed.astimezone(UTC)


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
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


@contextmanager
def _ledger_lock(path: Path) -> Iterator[None]:
    path = path.resolve(strict=False)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise PilotLedgerError("pilot ledger lock is unavailable") from exc
    try:
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _command(command: list[str], *, cwd: Path | None = None) -> str:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PilotLedgerError("pilot host provenance check failed") from exc


def _verify_host_provenance(
    revision: str,
    image_digest: str,
    *,
    repo: Path = DEFAULT_REPO,
    env_file: Path = DEFAULT_ENV_FILE,
    compose: Path = DEFAULT_COMPOSE,
) -> None:
    if socket.gethostname() != PILOT_HOST:
        raise PilotLedgerError("pilot commands must run on the approved Rise2 host")
    repo = repo.resolve()
    env_file = env_file.resolve()
    compose_path = compose if compose.is_absolute() else repo / compose
    compose_path = compose_path.resolve()
    _regular_file(env_file, "Rise2 environment file")
    _regular_file(compose_path, "Rise2 Compose file")
    if _command(["git", "rev-parse", "HEAD"], cwd=repo) != revision:
        raise PilotLedgerError("runtime checkout revision does not match the pilot ledger")
    if _command(["git", "status", "--porcelain"], cwd=repo):
        raise PilotLedgerError("runtime checkout must be clean")

    compose_base = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(compose_path),
    ]
    try:
        normalized = json.loads(
            _command([*compose_base, "config", "--format", "json"], cwd=repo)
        )
    except json.JSONDecodeError as exc:
        raise PilotLedgerError("normalized Rise2 Compose is invalid") from exc
    services = normalized.get("services")
    if not isinstance(services, dict):
        raise PilotLedgerError("normalized Rise2 Compose has no services")
    for service in ("api", "worker", "scheduler"):
        definition = services.get(service)
        image = definition.get("image") if isinstance(definition, dict) else None
        if not isinstance(image, str) or not image.endswith(f"@{image_digest}"):
            raise PilotLedgerError("configured application image does not match the pilot ledger")

    api_ids = [
        value
        for value in _command([*compose_base, "ps", "-q", "api"], cwd=repo).splitlines()
        if value
    ]
    if len(api_ids) != 1:
        raise PilotLedgerError("exactly one running Rise2 API container is required")
    api_id = api_ids[0]
    observed_image = _command(
        ["docker", "inspect", "-f", "{{.Config.Image}}", api_id], cwd=repo
    )
    observed_revision = _command(
        [
            "docker",
            "inspect",
            "-f",
            '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
            api_id,
        ],
        cwd=repo,
    )
    if not observed_image.endswith(f"@{image_digest}"):
        raise PilotLedgerError("running application image does not match the pilot ledger")
    if observed_revision != revision:
        raise PilotLedgerError("running application revision does not match the pilot ledger")


def _evidence_sha256(path: Path) -> str:
    path = _regular_file(path, "evidence artifact")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_evidence(path: Path) -> dict[str, Any]:
    path = _regular_file(path, "evidence artifact")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise PilotLedgerError("evidence artifact mode must be 0600")
    if info.st_size <= 2 or info.st_size > MAX_EVIDENCE_BYTES:
        raise PilotLedgerError("evidence artifact size is invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotLedgerError("evidence artifact is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PilotLedgerError("evidence artifact must be a JSON object")
    return value


def _parse_metric(raw: str) -> tuple[str, bool | int | float]:
    key, separator, value = raw.partition("=")
    if not separator:
        raise PilotLedgerError("metrics must use key=value")
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


def _validate_metric_names(name: str, metrics: Mapping[str, Any]) -> None:
    expected = METRICS_BY_CHECK[name]
    if set(metrics) != expected:
        raise PilotLedgerError(f"check {name} has invalid metric set")


def _metric_equal(expected: bool | int | float, observed: Any) -> bool:
    if isinstance(expected, bool):
        return type(observed) is bool and observed is expected
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        return False
    return math.isfinite(float(observed)) and float(observed) == float(expected)


def _validate_evidence_artifact(
    name: str,
    path: Path,
    metrics: Mapping[str, bool | int | float],
) -> None:
    evidence = _load_evidence(path)
    if name in {"load_1_slot", "load_2_slots"}:
        load = evidence.get("load")
        prometheus = evidence.get("prometheus")
        if not isinstance(load, dict) or not isinstance(prometheus, dict):
            raise PilotLedgerError(f"evidence for {name} has invalid aggregate structure")
        if load.get("schema") != LOAD_EVIDENCE_SCHEMA:
            raise PilotLedgerError(f"evidence for {name} has invalid schema")
        if load.get("secrets_or_business_identifiers_in_report") is not False:
            raise PilotLedgerError(f"evidence for {name} is not secret-safe")
        source = load
    else:
        expected_schema = EVIDENCE_SCHEMAS[name]
        if evidence.get("schema") != expected_schema:
            raise PilotLedgerError(f"evidence for {name} has invalid schema")
        if evidence.get("secrets_or_business_identifiers_in_report") is not False:
            raise PilotLedgerError(f"evidence for {name} is not secret-safe")
        source = evidence

    for key, expected in metrics.items():
        if not _metric_equal(expected, source.get(key)):
            raise PilotLedgerError(f"evidence for {name} does not substantiate metric {key}")


def _number(metrics: Mapping[str, Any], key: str) -> float:
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PilotLedgerError(f"required metric is missing or non-numeric: {key}")
    number = float(value)
    if not math.isfinite(number):
        raise PilotLedgerError(f"required metric is non-finite: {key}")
    return number


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
        if _number(metrics, "rto_seconds") != APPROVED_RTO_SECONDS:
            raise PilotLedgerError("restore RTO must equal the approved four-hour ceiling")
        if duration > APPROVED_RTO_SECONDS:
            raise PilotLedgerError("restore duration exceeds the approved RTO")
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
        if _number(metrics, "rto_seconds") != APPROVED_RTO_SECONDS:
            raise PilotLedgerError("rollback RTO must equal the approved four-hour ceiling")
        if duration > APPROVED_RTO_SECONDS:
            raise PilotLedgerError("rollback duration exceeds the approved RTO")
        _zero(metrics, "health_failures", "authentication_failures", "v1_writes")
        # Rollback intentionally permits internal idempotent drain/reconciliation. The safe
        # write boundary is public V2 admission suspension while V1 remains untouched.
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
    previous_time = _timestamp_value(report["created_at"], "created_at")
    checks = report["checks"]
    if not isinstance(checks, dict) or not set(checks).issubset(CHECKS):
        raise PilotLedgerError("pilot ledger contains unknown checks")

    recorded_count = 0
    for check in CHECKS:
        if check not in checks:
            break
        recorded_count += 1
    if set(checks) != set(CHECKS[:recorded_count]):
        raise PilotLedgerError("pilot checks must form the mandatory ordered prefix")

    for name in CHECKS[:recorded_count]:
        entry = checks[name]
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
            or not math.isfinite(float(duration))
            or duration < 0
        ):
            raise PilotLedgerError(f"check {name} has invalid duration")
        metrics = entry["metrics"]
        if not isinstance(metrics, dict):
            raise PilotLedgerError(f"check {name} has invalid metrics")
        _validate_metric_names(name, metrics)
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
        recorded_at = _timestamp_value(entry["recorded_at"], f"check {name} timestamp")
        if recorded_at <= previous_time:
            raise PilotLedgerError("pilot checks must have strictly increasing timestamps")
        previous_time = recorded_at
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
        decision_time = _timestamp_value(decision["recorded_at"], "pilot decision timestamp")
        if decision_time <= previous_time:
            raise PilotLedgerError("pilot decision must be recorded after the final check")
    if require_final:
        if recorded_count != len(CHECKS):
            missing = set(CHECKS) - set(checks)
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


def initialize(
    path: Path,
    revision: str,
    image_digest: str,
    *,
    repo: Path = DEFAULT_REPO,
    env_file: Path = DEFAULT_ENV_FILE,
    compose: Path = DEFAULT_COMPOSE,
) -> None:
    _verify_host_provenance(
        revision,
        image_digest,
        repo=repo,
        env_file=env_file,
        compose=compose,
    )
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
    with _ledger_lock(path):
        _write(path, report, create=True)


def record(
    path: Path,
    name: str,
    status_value: str,
    duration_seconds: float,
    evidence: Path,
    raw_metrics: list[str],
    *,
    repo: Path = DEFAULT_REPO,
    env_file: Path = DEFAULT_ENV_FILE,
    compose: Path = DEFAULT_COMPOSE,
) -> None:
    with _ledger_lock(path):
        report = _load(path)
        validate(report)
        if report["decision"] is not None:
            raise PilotLedgerError("a finalized pilot ledger cannot be changed")
        expected_name = (
            CHECKS[len(report["checks"])]
            if len(report["checks"]) < len(CHECKS)
            else None
        )
        if name != expected_name:
            raise PilotLedgerError("pilot checks must be recorded in the mandatory order")
        if (
            isinstance(duration_seconds, bool)
            or not math.isfinite(float(duration_seconds))
            or duration_seconds < 0
        ):
            raise PilotLedgerError("check duration must be a finite non-negative number")
        metrics = dict(_parse_metric(raw) for raw in raw_metrics)
        if len(metrics) != len(raw_metrics):
            raise PilotLedgerError("duplicate metric names are forbidden")
        _validate_metric_names(name, metrics)
        _verify_host_provenance(
            report["revision"],
            report["image_digest"],
            repo=repo,
            env_file=env_file,
            compose=compose,
        )
        _validate_evidence_artifact(name, evidence, metrics)
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
    with _ledger_lock(path):
        report = _load(path)
        validate(report)
        if report["decision"] is not None:
            raise PilotLedgerError("pilot ledger is already finalized")
        if REFERENCE_RE.fullmatch(approval_ref) is None:
            raise PilotLedgerError("approval reference is invalid")
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
    init_parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    init_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    init_parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("report", type=Path)
    record_parser.add_argument("check", choices=CHECKS)
    record_parser.add_argument("--status", choices=sorted(STATUSES), required=True)
    record_parser.add_argument("--duration-seconds", type=float, required=True)
    record_parser.add_argument("--evidence", type=Path, required=True)
    record_parser.add_argument("--metric", action="append", default=[])
    record_parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    record_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    record_parser.add_argument("--compose", type=Path, default=DEFAULT_COMPOSE)

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
            initialize(
                args.report,
                args.revision,
                args.image_digest,
                repo=args.repo,
                env_file=args.env_file,
                compose=args.compose,
            )
        elif args.command == "record":
            record(
                args.report,
                args.check,
                args.status,
                args.duration_seconds,
                args.evidence,
                args.metric,
                repo=args.repo,
                env_file=args.env_file,
                compose=args.compose,
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
