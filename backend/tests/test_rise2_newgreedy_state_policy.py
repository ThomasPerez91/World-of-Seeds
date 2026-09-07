from pathlib import Path


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def test_rise2_preflight_preserves_newgreedy_policy_across_qb_restarts() -> None:
    repository = _repository()
    preflight = (repository / "scripts" / "rise2_v2_preflight.sh").read_text(encoding="utf-8")
    policy = (repository / "scripts" / "rise2_v2_newgreedy_policy.py").read_text(encoding="utf-8")

    policy_call = "rise2_v2_newgreedy_policy.py"
    bootstrap = "rise2_v2_qb_bootstrap.py"

    assert policy_call in preflight
    assert preflight.index(policy_call) < preflight.index(bootstrap)

    assert 'parser.getint("proxy", "flow_detail")' in policy
    assert "if flow_detail != 0:" in policy
    assert 'parser.getboolean("stats", "persist_stats")' in policy
    assert 'parser.getboolean("stats", "auto_purge_stopped")' in policy
    assert "if not persist_stats:" in policy
    assert "if auto_purge_stopped:" in policy
