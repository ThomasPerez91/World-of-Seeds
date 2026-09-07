import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

BASH_TOOLS = (
    "rise2_v2_run_dependency_failure_gate.sh",
    "rise2_v2_run_resource_pressure_gate.sh",
    "rise2_v2_run_security_observability_gate.sh",
    "rise2_v2_run_test_data_cleanup_gate.sh",
    "rise2_v2_run_pilot_accounts_gate.sh",
    "rise2_v2_run_rollback_gate.sh",
    "rise2_v2_run_gates_7_12.sh",
)
PYTHON_PROBES = (
    "rise2_v2_dependency_failure_probe.py",
    "rise2_v2_resource_pressure_probe.py",
    "rise2_v2_data_hygiene_probe.py",
)


def _read(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_gates_7_12_tools_parse() -> None:
    for name in BASH_TOOLS:
        subprocess.run(["bash", "-n", str(SCRIPTS / name)], check=True)
    for name in PYTHON_PROBES:
        source = _read(name)
        compile(source, str(SCRIPTS / name), "exec")


def test_consolidated_runner_covers_every_remaining_gate() -> None:
    runner = _read("rise2_v2_run_gates_7_12.sh")

    for gate in (
        "dependency_failures",
        "resource_pressure",
        "security_observability",
        "test_data_cleanup",
        "pilot_accounts",
        "rollback",
    ):
        assert gate in runner
    assert "PILOT LEDGER = 12/12 PASSED" in runner
    assert "decision=NOT_RECORDED" in runner
    assert "failed_gate=$CURRENT_GATE" in runner
    assert "ledger_summary" in runner
    assert "--remove-orphans" not in runner
    assert "finalize" not in runner


def test_resource_pressure_gate_is_bounded_and_fail_closed() -> None:
    runner = _read("rise2_v2_run_resource_pressure_gate.sh")
    probe = _read("rise2_v2_resource_pressure_probe.py")

    assert "RAM_BYTES = 64 * 1024 * 1024" in probe
    assert "IO_BYTES = 64 * 1024 * 1024" in probe
    assert "CPU_SECONDS = 3.0" in probe
    assert 'rejection_code != "disk_pressure_critical"' in probe
    assert '"disk_pressure_fail_closed"' in probe
    assert '"unexplained_threshold_breaches":0' in runner
    assert '"cpu_ram_io_disk_observed":observed' in runner
    assert "--remove-orphans" not in runner


def test_security_observability_gate_uses_pinned_scanner_and_ci_proof() -> None:
    runner = _read("rise2_v2_run_security_observability_gate.sh")

    assert 'TRIVY_VERSION="0.74.0"' in runner
    assert "sha256sum --check --strict" in runner
    assert "head_sha=$TOOL_REV&event=pull_request" in runner
    assert 'run.get("name") == "CI"' in runner
    assert 'run.get("name") == "Rise2 qB bootstrap"' in runner
    assert '"private_metrics_available":private_available' in runner
    assert '"public_metrics_blocked":public_code == 404' in runner
    assert '"fixable_high_critical":config_high + image_fixable' in runner
    assert '"secret_findings":secret_findings' in runner
    assert '"business_identifier_findings":business_findings' in runner


def test_cleanup_and_pilot_account_gates_keep_credentials_private() -> None:
    cleanup = _read("rise2_v2_run_test_data_cleanup_gate.sh")
    pilot = _read("rise2_v2_run_pilot_accounts_gate.sh")
    probe = _read("rise2_v2_data_hygiene_probe.py")

    assert '"remaining_test_users"] == 0' in cleanup
    assert '"remaining_test_torrents"] == 0' in cleanup
    assert '"remaining_test_files"] == 0' in cleanup
    assert 'os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600' in probe
    assert "install -o root -g root -m 0600" in pilot
    assert "rm -f -- \"$TEMP_CREDENTIALS\"" in pilot
    assert '"credentials_in_output"] == 0' in pilot
    assert "cat \"$CREDENTIALS\"" not in pilot


def test_rollback_gate_is_reversible_and_preserves_v2_volumes() -> None:
    runner = _read("rise2_v2_run_rollback_gate.sh")

    assert 'V1_HEALTH_URL="https://world-of-seeds.fr/api/v1/health/live"' in runner
    assert "dc stop -t 15 ingress" in runner
    assert "dc start ingress" in runner
    assert '"v1_available":True' in runner
    assert '"v2_admission_suspended":True' in runner
    assert '"v2_volumes_preserved":True' in runner
    assert '"authentication_failures":0' in runner
    assert '"health_failures":0' in runner
    assert "--remove-orphans" not in runner
