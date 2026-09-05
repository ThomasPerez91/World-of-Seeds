from __future__ import annotations

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rise2_v2_cleanup_load_campaign.py"


def test_cleanup_tool_compiles_and_is_campaign_scoped() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    compile(source, str(SCRIPT), "exec")
    assert 'parser.add_argument("--campaign", required=True)' in source
    assert "helper._campaign(campaign)" in source
    assert "helper._prefix(campaign)" in source
    assert "ManagedTorrent.name.like(f\"{prefix}%\")" in source
    assert "User.username.like(f\"{prefix}%\")" in source


def test_cleanup_tool_uses_owned_qb_removal_and_verifies_zero_remainder() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "helper._remove_qbittorrent_fixtures(" in source
    assert "if any(after.values()):" in source
    assert '"remaining_users": after["users"]' in source
    assert '"remaining_torrents": after["torrents"]' in source
    assert '"remaining_requests": after["requests"]' in source
    assert '"secrets_or_business_identifiers_in_report": False' in source


def test_cleanup_tool_disposes_engine_in_same_asyncio_run() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "await engine.dispose()" in source
    assert "asyncio.run(engine.dispose())" not in source
