from __future__ import annotations

import runpy
from pathlib import Path

RUNNER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "rise2_v2_run_load_gate4.py"
RUNNER = runpy.run_path(str(RUNNER_PATH))


def test_gate4_resume_compiles_and_only_runs_two_slot_gate() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    compile(source, str(RUNNER_PATH), "exec")
    assert 'self.run_gate(2, "load_2_slots")' in source
    assert 'self.run_gate(1, "load_1_slot")' not in source
    assert '"--warmup-seconds",\n            "300"' in source
    assert '"--measurement-seconds",\n            "1800"' in source


def test_gate4_resume_requires_gate3_and_refuses_recorded_gate4() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    assert '("preflight", "backup_restore", "load_1_slot")' in source
    assert 'if "load_2_slots" in checks:' in source
    assert 'load_1_slot=RECORDED' in source


def test_gate4_resume_preserves_failed_report_before_validation() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    report_write = source.index("base.write_private_json(report_path, report)")
    aggregate_write = source.index(
        'base.write_private_json(aggregate_path, {"load": report, "prometheus": prom})'
    )
    validate = source.index("base.validate_load(report, slots)")
    assert report_write < aggregate_write < validate
    assert "completed.returncode not in (0, 2)" in source
    assert '(completed.returncode == 2) != failed' in source


def test_gate4_resume_keeps_control_plane_stopped_during_prepare_and_load() -> None:
    source = RUNNER_PATH.read_text(encoding="utf-8")
    stop = source.index("self.stop_control_plane()")
    prepare = source.index("self.prepare()")
    run_gate = source.index('self.run_gate(2, "load_2_slots")')
    restore = source.index("self.restore_control_plane()", run_gate)
    assert stop < prepare < run_gate < restore
    assert "--remove-orphans" not in source
