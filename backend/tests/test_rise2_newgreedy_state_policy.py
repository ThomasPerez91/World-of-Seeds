from pathlib import Path


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def test_rise2_preflight_preserves_newgreedy_stats_across_qb_restarts() -> None:
    script = (_repository() / "scripts" / "rise2_v2_preflight.sh").read_text(encoding="utf-8")

    persist_check = 'config.getboolean("stats", "persist_stats")'
    stopped_check = 'config.getboolean("stats", "auto_purge_stopped")'
    bootstrap = 'rise2_v2_qb_bootstrap.py'

    assert persist_check in script
    assert stopped_check in script
    assert "if not persist_stats or auto_purge_stopped:" in script
    assert (
        "NewGreedy stats policy requires persist_stats=true and auto_purge_stopped=false"
        in script
    )
    assert script.index(persist_check) < script.index(bootstrap)
    assert script.index(stopped_check) < script.index(bootstrap)
