from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_v2_release_candidate.py"


def test_v2_release_candidate_policy_is_valid() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert result.returncode == 0, result.stdout
    assert "V2 release candidate policy: PASS (2.0.0-rc.1)" in result.stdout


def test_v2_release_candidate_locks_previous_digest_and_schema() -> None:
    manifest = json.loads((ROOT / "deploy/v2-rc-manifest.json").read_text(encoding="utf-8"))
    assert manifest["pilot"]["previous_wos_image"] == (
        "ghcr.io/thomasperez91/world-of-seeds-v2@sha256:"
        "d0e817283ad95ba1792b4e16e7241bddabbce2190272382e2c339b4c291a947e"
    )
    assert manifest["database"] == {
        "pilot_revision": "20260831_22",
        "candidate_revision": "20260831_22",
    }


def test_v2_release_candidate_cannot_cross_stable_boundary() -> None:
    manifest = json.loads((ROOT / "deploy/v2-rc-manifest.json").read_text(encoding="utf-8"))
    assert manifest["functional_freeze"] is True
    assert manifest["release_policy"] == {
        "automatic_deploy": False,
        "dns_switch": False,
        "v1_import": False,
        "stable_release": False,
        "v1_rollback_preserved": True,
    }
