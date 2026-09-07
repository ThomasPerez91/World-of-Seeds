import importlib.util
import json
import multiprocessing
import stat
from pathlib import Path
from types import ModuleType
from typing import Any

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


@pytest.fixture
def host_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot, "_verify_host_provenance", lambda *args, **kwargs: None)


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
            "pilot_account_count": 1,
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


def _write_evidence(
    path: Path,
    name: str,
    metrics: dict[str, bool | int | float],
) -> Path:
    payload: dict[str, Any]
    if name in {"load_1_slot", "load_2_slots"}:
        payload = {
            "load": {
                "schema": pilot.LOAD_EVIDENCE_SCHEMA,
                **metrics,
                "secrets_or_business_identifiers_in_report": False,
            },
            "prometheus": {"query_errors": 0},
        }
    else:
        payload = {
            "schema": pilot.EVIDENCE_SCHEMAS[name],
            **metrics,
            "secrets_or_business_identifiers_in_report": False,
        }
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _raw_metrics(metrics: dict[str, bool | int | float]) -> list[str]:
    return [f"{key}={str(value).lower()}" for key, value in metrics.items()]


def _record_one(
    report: Path,
    root: Path,
    name: str,
    *,
    failed: bool = False,
    duration: float | None = None,
) -> None:
    metrics = _metrics(name)
    evidence = _write_evidence(root / f"{name}.json", name, metrics)
    pilot.record(
        report,
        name,
        "failed" if failed else "passed",
        duration if duration is not None else (2100 if name.startswith("load_") else 60),
        evidence,
        _raw_metrics(metrics),
    )


def _record_all(report: Path, root: Path, *, failed_check: str | None = None) -> None:
    for name in pilot.CHECKS:
        _record_one(report, root, name, failed=name == failed_check)


def test_complete_go_ledger_is_private_and_valid(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    pilot.initialize(report, REVISION, DIGEST)
    _record_all(report, tmp_path)
    pilot.finalize(report, "go", "v2-33-go-20260907")

    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    value = json.loads(report.read_text())
    pilot.validate(value, require_final=True)
    assert value["checks"]["preflight"]["evidence_sha256"]
    assert value["decision"]["status"] == "go"


def test_record_rejects_empty_or_unrelated_evidence(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    evidence = tmp_path / "unrelated.json"
    evidence.write_text("{}\n", encoding="utf-8")
    evidence.chmod(0o600)
    pilot.initialize(report, REVISION, DIGEST)

    with pytest.raises(pilot.PilotLedgerError, match="invalid schema"):
        pilot.record(
            report,
            "preflight",
            "passed",
            1,
            evidence,
            _raw_metrics(_metrics("preflight")),
        )


def test_record_rejects_evidence_metric_mismatch(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    metrics = _metrics("preflight")
    evidence_metrics = dict(metrics)
    evidence_metrics["policy_failures"] = 1
    evidence = _write_evidence(tmp_path / "preflight.json", "preflight", evidence_metrics)
    pilot.initialize(report, REVISION, DIGEST)

    with pytest.raises(pilot.PilotLedgerError, match="does not substantiate metric"):
        pilot.record(
            report,
            "preflight",
            "passed",
            1,
            evidence,
            _raw_metrics(metrics),
        )


def test_record_rejects_non_finite_duration(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    metrics = _metrics("preflight")
    evidence = _write_evidence(tmp_path / "preflight.json", "preflight", metrics)
    pilot.initialize(report, REVISION, DIGEST)

    with pytest.raises(pilot.PilotLedgerError, match="finite"):
        pilot.record(
            report,
            "preflight",
            "passed",
            float("nan"),
            evidence,
            _raw_metrics(metrics),
        )


def test_restore_rto_is_fixed_to_four_hours(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    pilot.initialize(report, REVISION, DIGEST)
    _record_one(report, tmp_path, "preflight")
    metrics = _metrics("backup_restore")
    metrics["rto_seconds"] = 20_000
    evidence = _write_evidence(tmp_path / "backup_restore.json", "backup_restore", metrics)

    with pytest.raises(pilot.PilotLedgerError, match="four-hour ceiling"):
        pilot.record(
            report,
            "backup_restore",
            "passed",
            19_000,
            evidence,
            _raw_metrics(metrics),
        )


def test_record_enforces_mandatory_check_order(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    metrics = _metrics("backup_restore")
    evidence = _write_evidence(tmp_path / "backup_restore.json", "backup_restore", metrics)
    pilot.initialize(report, REVISION, DIGEST)

    with pytest.raises(pilot.PilotLedgerError, match="mandatory order"):
        pilot.record(
            report,
            "backup_restore",
            "passed",
            1,
            evidence,
            _raw_metrics(metrics),
        )


def test_record_rejects_unknown_or_duplicate_metric_names(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    metrics = _metrics("preflight")
    evidence = _write_evidence(tmp_path / "preflight.json", "preflight", metrics)
    pilot.initialize(report, REVISION, DIGEST)

    with pytest.raises(pilot.PilotLedgerError, match="invalid metric set"):
        pilot.record(
            report,
            "preflight",
            "passed",
            1,
            evidence,
            [*_raw_metrics(metrics), "token_like_field=1"],
        )

    raw = _raw_metrics(metrics)
    with pytest.raises(pilot.PilotLedgerError, match="duplicate metric"):
        pilot.record(
            report,
            "preflight",
            "passed",
            1,
            evidence,
            [*raw, raw[0]],
        )


def test_approval_reference_uses_closed_operational_namespace(
    tmp_path: Path, host_ok: None
) -> None:
    report = tmp_path / "pilot.json"
    pilot.initialize(report, REVISION, DIGEST)
    _record_all(report, tmp_path)

    with pytest.raises(pilot.PilotLedgerError, match="approval reference"):
        pilot.finalize(report, "go", "ghp_abcdefghijklmnopqrstuvwxyz123456")


def test_go_refuses_missing_host_checks(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    pilot.initialize(report, REVISION, DIGEST)

    with pytest.raises(pilot.PilotLedgerError, match="missing checks"):
        pilot.finalize(report, "go", "ops-approval-20260907")


def test_no_go_accepts_complete_failed_host_matrix(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    pilot.initialize(report, REVISION, DIGEST)
    _record_all(report, tmp_path, failed_check="resource_pressure")

    pilot.finalize(report, "no_go", "ops-approval-20260907")
    pilot.validate(json.loads(report.read_text()), require_final=True)


def test_validate_rejects_out_of_order_timestamps(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    pilot.initialize(report, REVISION, DIGEST)
    _record_one(report, tmp_path, "preflight")
    _record_one(report, tmp_path, "backup_restore")
    value = json.loads(report.read_text())
    value["checks"]["backup_restore"]["recorded_at"] = value["checks"]["preflight"]["recorded_at"]

    with pytest.raises(pilot.PilotLedgerError, match="strictly increasing"):
        pilot.validate(value)


def test_ledger_rejects_symlinked_evidence(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    target = _write_evidence(tmp_path / "preflight.json", "preflight", _metrics("preflight"))
    link = tmp_path / "evidence-link.json"
    link.symlink_to(target)
    pilot.initialize(report, REVISION, DIGEST)

    with pytest.raises(pilot.PilotLedgerError, match="regular file"):
        pilot.record(
            report,
            "preflight",
            "passed",
            1,
            link,
            _raw_metrics(_metrics("preflight")),
        )


def _concurrent_record_worker(report: str, evidence: str, queue: Any) -> None:
    module = _pilot_module()
    module.__dict__["_verify_host_provenance"] = lambda *args, **kwargs: None
    metrics = _metrics("preflight")
    try:
        module.record(
            Path(report),
            "preflight",
            "passed",
            1,
            Path(evidence),
            _raw_metrics(metrics),
        )
    except module.PilotLedgerError:
        queue.put("rejected")
    else:
        queue.put("recorded")


def test_concurrent_record_is_serialized_without_lost_update(tmp_path: Path, host_ok: None) -> None:
    report = tmp_path / "pilot.json"
    evidence = _write_evidence(tmp_path / "preflight.json", "preflight", _metrics("preflight"))
    pilot.initialize(report, REVISION, DIGEST)
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_record_worker,
            args=(str(report), str(evidence), queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    outcomes = sorted(queue.get(timeout=2) for _ in processes)
    assert outcomes == ["recorded", "rejected"]
    value = json.loads(report.read_text())
    assert set(value["checks"]) == {"preflight"}


def test_host_provenance_verifies_hostname_checkout_compose_and_running_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    env_file = tmp_path / "environment"
    env_file.write_text("safe=true\n", encoding="utf-8")
    compose = repo / "compose.yaml"
    compose.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(pilot.socket, "gethostname", lambda: pilot.PILOT_HOST)

    expected_image = f"ghcr.io/example/wos@{DIGEST}"

    def fake_command(command: list[str], *, cwd: Path | None = None) -> str:
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return REVISION
        if command[:3] == ["git", "status", "--porcelain"]:
            return ""
        if "config" in command:
            return json.dumps(
                {
                    "services": {
                        name: {"image": expected_image} for name in ("api", "worker", "scheduler")
                    }
                }
            )
        if "ps" in command:
            return "api-container"
        if command[:3] == ["docker", "inspect", "-f"]:
            if "org.opencontainers.image.revision" in command[3]:
                return REVISION
            return expected_image
        raise AssertionError(command)

    monkeypatch.setattr(pilot, "_command", fake_command)
    pilot._verify_host_provenance(
        REVISION,
        DIGEST,
        repo=repo,
        env_file=env_file,
        compose=Path("compose.yaml"),
    )


def test_pilot_tool_is_executable_and_runbook_covers_every_check() -> None:
    source = (Path(__file__).resolve().parents[2] / "scripts" / "rise2_v2_pilot.py").read_text(
        encoding="utf-8"
    )
    compile(source, "rise2_v2_pilot.py", "exec")
    assert "fcntl.flock" in source
    assert "APPROVED_RTO_SECONDS = 14_400" in source
    assert "_validate_evidence_artifact" in source
    assert "_verify_host_provenance" in source
