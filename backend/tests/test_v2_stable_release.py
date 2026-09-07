import json
import subprocess
import sys
from pathlib import Path


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def test_v2_stable_release_policy_passes() -> None:
    repository = _repository()
    result = subprocess.run(
        [sys.executable, str(repository / "scripts/validate_v2_stable_release.py")],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "V2 stable release policy: PASS (2.0.0)" in result.stdout


def test_v2_stable_manifest_is_anchored_to_the_validated_rc() -> None:
    repository = _repository()
    manifest = json.loads(
        (repository / "deploy/v2-stable-manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["version"] == "2.0.0"
    assert manifest["source_release_candidate"] == {
        "version": "2.0.0-rc.1",
        "revision": "896323ac1858a804ce5a6d33185f3b30b7db3847",
        "image": (
            "ghcr.io/thomasperez91/world-of-seeds-v2@sha256:"
            "47f0e7ba7ca2f5700e94def18bac91748cefcaac6b61ed4225573b4aa6d1afcd"
        ),
        "rise2_validation": "passed",
        "rollback_seconds": 15,
    }
    assert manifest["database"]["release_candidate_revision"] == "20260831_22"
    assert manifest["database"]["stable_revision"] == "20260831_22"
    assert manifest["release_policy"] == {
        "automatic_deploy": False,
        "automatic_dns_switch": False,
        "automatic_v1_import": False,
        "progressive_cutover_requires_explicit_approval": True,
        "v1_rollback_preserved": True,
        "rebuild_after_stable_validation": False,
    }


def test_v2_stable_merge_has_no_automatic_stable_promotion_or_v1_side_effect() -> None:
    repository = _repository()
    workflows = repository / ".github/workflows"

    v2_image = (workflows / "v2-image.yml").read_text(encoding="utf-8")
    v2_gate = (workflows / "v2-rc.yml").read_text(encoding="utf-8")
    v1_release = (workflows / "release.yml").read_text(encoding="utf-8")
    v1_deploy = (workflows / "deploy.yml").read_text(encoding="utf-8")

    assert "world-of-seeds-v2:sha-" in v2_image
    assert "world-of-seeds-v2:2.0.0" not in v2_image
    assert "world-of-seeds-v2:latest" not in v2_image
    assert "validate_v2_stable_release.py" in v2_gate
    assert "develop_V2" not in v1_release
    assert "develop_V2" not in v1_deploy


def test_v2_stable_runbook_keeps_cutover_and_v1_retirement_explicit() -> None:
    repository = _repository()
    runbook = (repository / "docs/release-v2.md").read_text(encoding="utf-8")

    assert "## Progressive Rise2 cutover" in runbook
    assert "Production traffic switch: separate explicit approval." in runbook
    assert "V1 import: separate explicit approval" in runbook
    assert "V1 retirement: out of scope" in runbook
    assert "do not repeat the long" in runbook
