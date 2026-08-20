import subprocess
import sys
from pathlib import Path


def test_application_version_sources_match_the_canonical_version() -> None:
    repository = Path(__file__).resolve().parents[2]
    version = (repository / "VERSION").read_text(encoding="utf-8").strip()

    result = subprocess.run(
        [
            sys.executable,
            str(repository / "scripts/versioning.py"),
            "--root",
            str(repository),
            "check",
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
    repository = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [
            sys.executable,
            str(repository / "scripts/versioning.py"),
            "--root",
            str(repository),
            "check",
            "--expected-tag",
            "v0.0.0",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "does not match VERSION" in result.stderr
