import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "rise2_v2_run_dependency_failure_gate.sh"
PROBE_PATH = ROOT / "scripts" / "rise2_v2_dependency_failure_probe.py"


def test_dependency_failure_gate_tools_parse() -> None:
    subprocess.run(["bash", "-n", str(RUNNER_PATH)], check=True)
    source = PROBE_PATH.read_text(encoding="utf-8")
    compile(source, str(PROBE_PATH), "exec")


def test_dependency_failure_gate_covers_all_required_failure_families() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")

    assert 'echo "scenario=1/8 redis_unavailable"' in runner
    assert 'echo "scenario=2/8 postgres_stall"' in runner
    assert 'echo "scenario=3/8 qbittorrent_unavailable"' in runner
    assert 'echo "scenario=4/8 newgreedy_unavailable"' in runner
    assert 'echo "scenario=5/8 worker_outage"' in runner
    assert 'echo "scenario=6/8 scheduler_outage"' in runner
    assert 'echo "scenario=7/8 qbittorrent_reset"' in runner
    assert 'echo "scenario=8/8 ingress_api_outage"' in runner
    assert '"scenarios":8' in runner
    assert '"false_successes":0' in runner
    assert '"lost_jobs":0' in runner
    assert '"recovery_failures":0' in runner
    assert '"idempotent_recovery":True' in runner


def test_dependency_failure_gate_proves_backoff_and_durable_job_recovery() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")
    probe = PROBE_PATH.read_text(encoding="utf-8")

    assert 'RECOVERY_JOB_TYPE = "V233_RECOVERY_CANARY"' in probe
    assert "claim_expires_at=now - timedelta(minutes=1)" in probe
    assert 'recovery.last_error_code == "claim_expired"' in probe
    assert '"recovery_canary_backoff"' in probe
    assert "wait_canary" in runner
    assert 'echo "expired_job_recovery=PASSED backoff=PASSED"' in runner
    assert '"backoff_verified":True' in runner
    assert '"durable_queue_preserved":True' in runner


def test_dependency_failure_gate_is_pinned_secret_safe_and_bounded() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")
    probe = PROBE_PATH.read_text(encoding="utf-8")

    assert "runner tool blob mismatch" in runner
    assert "application control-plane provenance mismatch" in runner
    assert 'assert "dependency_failures" not in p["checks"]' in runner
    assert '"transfer_manifest"' in runner
    assert "refusing to touch a container outside the Rise2 V2 project" in runner
    assert 'docker pause "$PG_CID"' in runner
    assert 'docker unpause "$cid"' in runner
    assert 'dc stop -t 15 "$1"' in runner
    assert "--remove-orphans" not in runner
    assert '"secrets_or_business_identifiers_in_report":False' in runner
    assert 'delete(ManagedTorrent).where(ManagedTorrent.name.like(f"{name_prefix}%"))' in probe
