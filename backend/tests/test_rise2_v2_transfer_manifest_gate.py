from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "rise2_v2_run_transfer_manifest_gate.py"
PROBE_PATH = ROOT / "scripts" / "rise2_v2_transfer_manifest_probe.py"


def test_transfer_manifest_gate_tools_compile() -> None:
    for path in (RUNNER_PATH, PROBE_PATH):
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")


def test_transfer_manifest_gate_enforces_required_operational_proof() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")
    probe = PROBE_PATH.read_text(encoding="utf-8")

    assert '"manifest_file_count"' in runner
    assert 'result["manifest_file_count"] < 50_000' in runner
    assert '"integrity_failures"' in runner
    assert '"residual_leases"' in runner
    assert '"limit_violations"' in runner
    assert '"progressive_start"' in runner
    assert '"pause_resume_cancel_verified"' in runner
    assert "MANIFEST_FILE_COUNT = 50_000" in probe
    assert "MANIFEST_PAGE_SIZE = 500" in probe
    assert "for offset in range(0, MANIFEST_FILE_COUNT, MANIFEST_PAGE_SIZE)" in probe
    assert 'headers={"Range": f"bytes=0-{midpoint - 1}", "If-Range": etag}' in probe
    assert 'response.iter_raw(chunk_size=64 * 1024)' in probe
    assert "await _wait_for_no_leases" in probe


def test_transfer_manifest_gate_is_pinned_secret_safe_and_cleanup_bounded() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")
    probe = PROBE_PATH.read_text(encoding="utf-8")

    assert "tool blob mismatch" in runner
    assert "application control-plane provenance mismatch" in runner
    assert '"websocket_recovery"' in runner
    assert 'if "transfer_manifest" in checks:' in runner
    assert '"secrets_or_business_identifiers_in_report"' in runner
    assert 'self.run_probe("cleanup", capture=True)' in runner
    assert "--remove-orphans" not in runner
    assert "ThreadPoolExecutor" not in probe
    assert "store.purge(storage_key)" in probe
    assert 'print(json.dumps(result, sort_keys=True))' in probe
