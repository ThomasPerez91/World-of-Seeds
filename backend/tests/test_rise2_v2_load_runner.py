from __future__ import annotations

import runpy
from pathlib import Path

RUNNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "rise2_v2_run_load_gates.py"
RUNNER = runpy.run_path(str(RUNNER_PATH))


def test_load_runner_compiles_and_has_bounded_campaign_pattern() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    compile(source, str(RUNNER_PATH), "exec")
    campaign_re = RUNNER["CAMPAIGN_RE"]
    assert campaign_re.fullmatch("g34-09050311")
    assert campaign_re.fullmatch("bad_name") is None


def test_load_runner_keeps_worker_and_scheduler_off_for_prepare_and_both_gates() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    stop = source.index('self.dc("stop", "worker", "scheduler")')
    prepare = source.index("self.prepare()")
    gate_one = source.index('self.run_gate(1, "load_1_slot")')
    gate_two = source.index('self.run_gate(2, "load_2_slots")')
    restart = source.index("self.restore_control_plane()", gate_two)
    assert stop < prepare < gate_one < gate_two < restart


def test_load_runner_uses_runtime_wrapper_and_secret_safe_registry_loader() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert "rise2_v2_scheduler_load_runtime.py" in source
    assert "/run/secrets/integration_registry" in source
    assert "WOS_INTEGRATION_ACCOUNTS_JSON" in source
    assert "WOS_RISE2_PILOT_ACK=V2-33" in source
    assert "--remove-orphans" not in source


def test_load_runner_enforces_full_gate_windows_and_invariants() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert '"--warmup-seconds",\n            "300"' in source
    assert '"--measurement-seconds",\n            "1800"' in source
    assert 'report.get("warmup_seconds", 0) >= 300' in source
    assert 'report.get("measurement_seconds", 0) >= 1800' in source
    assert 'report.get("duration_seconds", 0) >= 2100' in source
    assert 'report.get("famine_count") == 0' in source
    assert 'report.get("duplicate_count") == 0' in source
    assert 'report.get("corruption_count") == 0' in source
    assert 'report.get("unexpected_transition_count") == 0' in source


def test_load_runner_preserves_prometheus_and_aggregate_evidence_without_ledger_write() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert "host_cpu_peak_ratio" in source
    assert "host_iowait_peak_ratio" in source
    assert "host_memory_peak_ratio" in source
    assert "host_load1_peak" in source
    assert 'f"{stem}.prometheus.json"' in source
    assert 'f"{stem}.aggregate.json"' in source
    assert 'print("ledger_recorded=false")' in source
    assert "pilot_tool" not in source
