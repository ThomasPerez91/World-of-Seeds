import importlib.util
import json
import stat
from pathlib import Path
from types import ModuleType

import pytest


def _pilot_module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts" / "rise2_v2_pilot.py"
    spec = importlib.util.spec_from_file_location("rise2_v2_pilot", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot = _pilot_module()
REVISION = "a" * 40
DIGEST = f"sha256:{'b' * 64}"


def _metrics(name: str) -> dict[str, bool | int | float]:
    values: dict[str, dict[str, bool | int | float]] = {
        "preflight": {
            "newgreedy_readable": True,
            "isolated_v2_storage": True,
            "policy_failures": 0,
            "v1_mounts": 0,
            "public_internal_ports": 0,
        },
        "backup_restore": {
            "postgres_restored": True,
            "content_canary_verified": True,
            "restore_failures": 0,
            "secret_findings": 0,
            "existing_target_writes": 0,
            "rto_seconds": 14_400,
        },
        "load_1_slot": {
            "slots": 1,
            "warmup_seconds": 300,
            "measurement_seconds": 1800,
            "famine_count": 0,
            "duplicate_count": 0,
            "corruption_count": 0,
            "unexpected_transition_count": 0,
            "scheduler_cycle_p95_seconds": 4,
            "scheduler_interval_seconds": 5,
        },
        "load_2_slots": {
            "slots": 2,
            "warmup_seconds": 300,
            "measurement_seconds": 1800,
            "famine_count": 0,
            "duplicate_count": 0,
            "corruption_count": 0,
            "unexpected_transition_count": 0,
            "scheduler_cycle_p95_seconds": 4,
            "scheduler_interval_seconds": 5,
        },
        "websocket_recovery": {
            "connections": 100,
            "reconnections": 25,
            "idle_transactions": 0,
            "resync_failures": 0,
            "lost_events_after_resync": 0,
            "memory_returned_to_plateau": True,
        },
        "transfer_manifest": {
            "manifest_file_count": 50_000,
            "integrity_failures": 0,
            "residual_leases": 0,
            "limit_violations": 0,
            "progressive_start": True,
            "pause_resume_cancel_verified": True,
        },
        "dependency_failures": {
            "scenarios": 8,
            "false_successes": 0,
            "lost_jobs": 0,
            "recovery_failures": 0,
            "idempotent_recovery": True,
        },
        "resource_pressure": {
            "admission_failures": 0,
            "unexplained_threshold_breaches": 0,
            "cpu_ram_io_disk_observed": True,
            "disk_pressure_fail_closed": True,
        },
        "security_observability": {
            "fixable_high_critical": 0,
            "secret_findings": 0,
            "business_identifier_findings": 0,
            "public_metrics_blocked": True,
            "private_metrics_available": True,
        },
        "test_data_cleanup": {
            "remaining_test_users": 0,
            "remaining_test_torrents": 0,
            "remaining_test_files": 0,
            "v1_unchanged": True,
        },
        "pilot_accounts": {
            "pilot_account_count": 3,
            "v1_data_moves": 0,
            "credentials_in_output": 0,
            "forced_credential_change": True,
            "v1_unchanged": True,
        },
        "rollback": {
            "rto_seconds": 14_400,
            "health_failures": 0,
            "authentication_failures": 0,
            "v1_writes": 0,
            "v1_available": True,
            "v2_admission_suspended": True,
            "v2_volumes_preserved": True,
        },
    }
    return values[name]


def _record_all(
    report: Path,
    evidence: Path,
    *,
    failed_check: str | None = None,
) -> None:
    for name in pilot.CHECKS:
        metrics = _metrics(name)
        raw_metrics = [f"{key}={str(value).lower()}" for key, value in metrics.items()]
        pilot.record(
            report,
            name,
            "failed" if name == failed_check else "passed",
            2100 if name.startswith("load_") else 60,
            evidence,
            raw_metrics,
        )


def test_complete_go_ledger_is_private_and_valid(tmp_path: Path) -> None:
    report = tmp_path / "pilot.json"
    evidence = tmp_path / "aggregate.json"
    evidence.write_text('{"aggregate": true}\n')

    pilot.initialize(report, REVISION, DIGEST)
    _record_all(report, evidence)
    pilot.finalize(report, "go", "ops-approval-20260830")

    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    value = json.loads(report.read_text())
    pilot.validate(value, require_final=True)
    assert value["checks"]["preflight"]["evidence_sha256"]
    assert str(evidence) not in report.read_text()
    assert "aggregate" not in report.read_text()


def test_go_refuses_missing_host_checks(tmp_path: Path) -> None:
    report = tmp_path / "pilot.json"
    pilot.initialize(report, REVISION, DIGEST)

    with pytest.raises(pilot.PilotLedgerError, match="missing checks"):
        pilot.finalize(report, "go", "ops-approval-20260830")


def test_load_pass_refuses_short_measurement(tmp_path: Path) -> None:
    report = tmp_path / "pilot.json"
    evidence = tmp_path / "aggregate.json"
    evidence.write_text("{}\n")
    pilot.initialize(report, REVISION, DIGEST)
    metrics = _metrics("load_1_slot")
    metrics["measurement_seconds"] = 1799

    with pytest.raises(pilot.PilotLedgerError, match="30 minutes"):
        pilot.record(
            report,
            "load_1_slot",
            "passed",
            2100,
            evidence,
            [f"{key}={value}" for key, value in metrics.items()],
        )


def test_no_go_requires_a_recorded_failure(tmp_path: Path) -> None:
    report = tmp_path / "pilot.json"
    evidence = tmp_path / "aggregate.json"
    evidence.write_text("{}\n")
    pilot.initialize(report, REVISION, DIGEST)
    _record_all(report, evidence)

    with pytest.raises(pilot.PilotLedgerError, match="at least one failed check"):
        pilot.finalize(report, "no_go", "ops-approval-20260830")


def test_no_go_accepts_a_complete_failed_host_matrix(tmp_path: Path) -> None:
    report = tmp_path / "pilot.json"
    evidence = tmp_path / "aggregate.json"
    evidence.write_text("{}\n")
    pilot.initialize(report, REVISION, DIGEST)
    _record_all(report, evidence, failed_check="resource_pressure")

    pilot.finalize(report, "no_go", "ops-approval-20260830")
    pilot.validate(json.loads(report.read_text()), require_final=True)


def test_ledger_rejects_symlinked_evidence(tmp_path: Path) -> None:
    report = tmp_path / "pilot.json"
    evidence = tmp_path / "aggregate.json"
    evidence.write_text("{}\n")
    link = tmp_path / "evidence-link.json"
    link.symlink_to(evidence)
    pilot.initialize(report, REVISION, DIGEST)

    with pytest.raises(pilot.PilotLedgerError, match="regular file"):
        pilot.record(report, "preflight", "failed", 1, link, [])


def test_pilot_tool_is_executable_and_runbook_covers_every_check() -> None:
    repository = Path(__file__).resolve().parents[2]
    script = repository / "scripts" / "rise2_v2_pilot.py"
    runbook = (repository / "docs" / "pilot-rise2-v2.md").read_text(encoding="utf-8")

    assert script.stat().st_mode & stat.S_IXUSR
    for name in pilot.CHECKS:
        assert f"`{name}`" in runbook
