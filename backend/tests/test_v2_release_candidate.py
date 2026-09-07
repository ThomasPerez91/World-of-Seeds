from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_v2_release_candidate.py"


def test_v2_release_candidate_validator_rejects_the_stable_checkout() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert result.returncode == 1
    assert "VERSION must be a 2.0.0 release candidate" in result.stdout


def test_v2_release_candidate_pins_approved_pilot_evidence() -> None:
    manifest = json.loads((ROOT / "deploy/v2-rc-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2.0.0-rc.1"
    assert manifest["pilot"] == {
        "approval_ref": "v2-33-go-20260907",
        "ledger_sha256": "38c94b41aed849a754053470e4a1eba8834157c64c57c6fb2e7d79dcca19d70b",
        "runtime_revision": "adcf67d5ea92b72c2a2210f8cdafb29669a940d8",
        "previous_wos_image": (
            "ghcr.io/thomasperez91/world-of-seeds-v2@sha256:"
            "d0e817283ad95ba1792b4e16e7241bddabbce2190272382e2c339b4c291a947e"
        ),
    }


def test_v2_release_candidate_locks_pilot_migration_tree() -> None:
    manifest = json.loads((ROOT / "deploy/v2-rc-manifest.json").read_text(encoding="utf-8"))
    assert manifest["database"] == {
        "pilot_revision": "20260831_22",
        "candidate_revision": "20260831_22",
        "pilot_versions_tree_sha": "12c3a629ecf105797afd787d06f6c1e9d2b24d5b",
    }


def test_v2_release_candidate_evidence_remains_closed_after_stable_promotion() -> None:
    manifest = json.loads((ROOT / "deploy/v2-rc-manifest.json").read_text(encoding="utf-8"))
    assert manifest["functional_freeze"] is True
    assert manifest["release_policy"] == {
        "automatic_deploy": False,
        "dns_switch": False,
        "v1_import": False,
        "stable_release": False,
        "v1_rollback_preserved": True,
    }
