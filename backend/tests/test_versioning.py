import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def test_application_version_sources_match_the_canonical_version() -> None:
    repository = _repository()
    version = (repository / "VERSION").read_text(encoding="utf-8").strip()

    result = subprocess.run(
        [
            sys.executable,
            str(repository / "scripts/versioning.py"),
            "--root",
            str(repository),
            "check",
            "--channel",
            "v2",
            "--expected-version",
            version,
            "--expected-tag",
            f"v{version}",
            "--print-version",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == version


def test_application_version_rejects_a_mismatched_release_tag() -> None:
    repository = _repository()

    result = subprocess.run(
        [
            sys.executable,
            str(repository / "scripts/versioning.py"),
            "--root",
            str(repository),
            "check",
            "--channel",
            "v2",
            "--expected-tag",
            "v0.0.0",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "does not match VERSION" in result.stderr


def test_stable_channel_accepts_the_v2_stable_release() -> None:
    repository = _repository()

    result = subprocess.run(
        [
            sys.executable,
            str(repository / "scripts/versioning.py"),
            "--root",
            str(repository),
            "check",
            "--expected-version",
            "2.0.0",
            "--expected-tag",
            "v2.0.0",
            "--print-version",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "2.0.0"


@pytest.mark.parametrize(
    ("version", "channel"),
    [
        ("1.3.3", "v2"),
        ("2.0.0-preview.1", "v2"),
        ("2.0.0-alpha.01", "v2"),
        ("2.0.0-alpha.0", "stable"),
    ],
)
def test_version_channels_reject_incompatible_formats(version: str, channel: str) -> None:
    namespace = runpy.run_path(str(_repository() / "scripts/versioning.py"))

    with pytest.raises(namespace["VersioningError"]):
        namespace["validate_version_format"](version, channel)


@pytest.mark.parametrize(
    "version",
    [
        "2.0.0-alpha.0",
        "2.0.0-beta.12",
        "2.0.0-rc.1",
        "2.4.1-alpha.3",
        "2.0.0",
        "2.4.1",
        "10.3.7",
    ],
)
def test_v2_channel_accepts_documented_v2_formats(version: str) -> None:
    namespace = runpy.run_path(str(_repository() / "scripts/versioning.py"))

    namespace["validate_version_format"](version, "v2")


def test_stable_channel_keeps_accepting_v1_versions() -> None:
    namespace = runpy.run_path(str(_repository() / "scripts/versioning.py"))

    namespace["validate_version_format"]("1.3.3", "stable")


def test_active_workflows_keep_ci_and_master_deploy_gate() -> None:
    workflows = _repository() / ".github/workflows"
    ci = (workflows / "ci.yml").read_text(encoding="utf-8")
    deploy = (workflows / "deploy-v2-rise2.yml").read_text(encoding="utf-8")

    assert '--channel "$WOS_VERSION_CHANNEL"' in ci
    assert 'workflows: ["CI"]' in deploy
    assert "github.event.workflow_run.conclusion == 'success'" in deploy
    assert "github.event.workflow_run.head_branch == 'master'" in deploy

    for retired in ("deploy.yml", "release.yml", "v2-image.yml", "v2-rc.yml"):
        assert not (workflows / retired).exists()
