from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT / "scripts" / "rise2_v2_run_websocket_gate.py"
PROBE_PATH = ROOT / "scripts" / "rise2_v2_websocket_probe.py"


def test_websocket_gate_tools_compile() -> None:
    for path in (RUNNER_PATH, PROBE_PATH):
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")


def test_websocket_gate_enforces_required_operational_proof() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")
    probe = PROBE_PATH.read_text(encoding="utf-8")

    assert '"connections": 100' in runner
    assert '"subscription_ready": 100' in runner
    assert '"reconnections": 25' in runner
    assert '"idle_transactions": 0' in runner
    assert '"resync_failures": 0' in runner
    assert '"lost_events_after_resync": 0' in runner
    assert '"memory_returned_to_plateau": True' in runner
    assert "tiers = (10, 25, 50, 100)" in probe
    assert 'receive_type(socket_, "heartbeat", timeout=25)' in probe
    assert "tcp_socket.settimeout(None)" in probe
    assert "reconnect subscriptions not ready" in probe
    assert "pg_stat_activity" in probe
    assert "resync_required" in probe
    assert '"/api/v2/torrents"' in probe
    assert '"authoritative_resync_successes"' in probe


def test_websocket_gate_preserves_secret_free_baseline_diagnostics() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")

    assert '"websocket_recovery.baseline.json"' in runner
    assert 'print(f"baseline_metrics={json.dumps(baseline_metrics, sort_keys=True)}")' in runner
    assert 'baseline.get("subscription_ready") != 100' in runner
    assert "baseline WebSocket invariants failed:" in runner


def test_websocket_gate_recovers_dependencies_and_runtime_exactly() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")

    assert 'self.dc("up", "-d", "--no-deps", "--force-recreate", "api")' in runner
    assert 'self.dc("stop", "redis")' in runner
    assert 'self.dc("start", "redis")' in runner
    assert "application control-plane provenance mismatch" in runner
    assert "tool blob mismatch" in runner
    assert "--remove-orphans" not in runner


def test_websocket_gate_requires_first_four_gates_and_keeps_evidence_secret_free() -> None:
    runner = RUNNER_PATH.read_text(encoding="utf-8")

    assert '("preflight", "backup_restore", "load_1_slot", "load_2_slots")' in runner
    assert 'if "websocket_recovery" in checks:' in runner
    assert '"secrets_or_business_identifiers_in_report": False' in runner
    assert "sessions.json" in runner
    assert "0o600" in runner
    assert 'print("ledger_recorded=false")' in runner
