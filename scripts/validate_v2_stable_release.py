#!/usr/bin/env python3
"""Validate the closed V2-35 stable-release policy without deployment side effects."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path("deploy/v2-stable-manifest.json")
RUNBOOK_PATH = Path("docs/release-v2.md")

EXPECTED_SCHEMA = "world-of-seeds-v2-stable-release/v1"
EXPECTED_VERSION = "2.0.0"
EXPECTED_RC_VERSION = "2.0.0-rc.1"
EXPECTED_RC_REVISION = "896323ac1858a804ce5a6d33185f3b30b7db3847"
EXPECTED_RC_IMAGE = (
    "ghcr.io/thomasperez91/world-of-seeds-v2@sha256:"
    "47f0e7ba7ca2f5700e94def18bac91748cefcaac6b61ed4225573b4aa6d1afcd"
)
EXPECTED_PREVIOUS_IMAGE = (
    "ghcr.io/thomasperez91/world-of-seeds-v2@sha256:"
    "d0e817283ad95ba1792b4e16e7241bddabbce2190272382e2c339b4c291a947e"
)
EXPECTED_DATABASE_REVISION = "20260831_22"
EXPECTED_MIGRATION_TREE = "12c3a629ecf105797afd787d06f6c1e9d2b24d5b"
EXPECTED_POLICY = {
    "automatic_deploy": False,
    "automatic_dns_switch": False,
    "automatic_v1_import": False,
    "progressive_cutover_requires_explicit_approval": True,
    "v1_rollback_preserved": True,
    "rebuild_after_stable_validation": False,
}

ALLOWED_RC_DELTA = {
    ".github/workflows/v2-image.yml",
    ".github/workflows/v2-rc.yml",
    "VERSION",
    "backend/app/__init__.py",
    "backend/pyproject.toml",
    "backend/tests/test_v2_release_candidate.py",
    "backend/tests/test_v2_stable_release.py",
    "backend/tests/test_versioning.py",
    "backend/uv.lock",
    "deploy/v2-stable-manifest.json",
    "docs/agent/PROGRESS.md",
    "docs/release-v2.md",
    "frontend/package-lock.json",
    "frontend/package.json",
    "frontend/src/version.ts",
    "scripts/validate_v2_stable_release.py",
    "scripts/versioning.py",
}


class StableReleaseError(RuntimeError):
    """A V2-35 stable-release invariant failed."""


def _run(root: Path, *command: str) -> str:
    result = subprocess.run(
        list(command),
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise StableReleaseError(f"command failed: {' '.join(command)}")
    return result.stdout.strip()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StableReleaseError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise StableReleaseError(f"JSON root must be an object: {path}")
    return value


def _closed_keys(value: dict[str, Any], expected: set[str], description: str) -> None:
    if set(value) != expected:
        raise StableReleaseError(f"{description} has unexpected keys")


def _validate_provenance(root: Path) -> None:
    try:
        _run(root, "git", "merge-base", "--is-ancestor", EXPECTED_RC_REVISION, "HEAD")
    except StableReleaseError as exc:
        raise StableReleaseError("stable branch is not descended from the validated RC") from exc

    changed = {
        line
        for line in _run(
            root,
            "git",
            "diff",
            "--name-only",
            f"{EXPECTED_RC_REVISION}..HEAD",
        ).splitlines()
        if line
    }
    unexpected = changed - ALLOWED_RC_DELTA
    if unexpected:
        raise StableReleaseError(
            "functional or unapproved files changed after the validated RC: "
            + ", ".join(sorted(unexpected))
        )
    if not changed:
        raise StableReleaseError("stable branch contains no release delta")


def _validate_version(root: Path, manifest: dict[str, Any]) -> None:
    try:
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise StableReleaseError("VERSION is unavailable") from exc
    if version != EXPECTED_VERSION or manifest.get("version") != EXPECTED_VERSION:
        raise StableReleaseError("V2-35 must be exactly version 2.0.0")

    for channel in ("v2", "stable"):
        _run(
            root,
            sys.executable,
            "scripts/versioning.py",
            "check",
            "--channel",
            channel,
            "--expected-version",
            EXPECTED_VERSION,
            "--expected-tag",
            "v2.0.0",
        )


def _validate_manifest(manifest: dict[str, Any]) -> None:
    _closed_keys(
        manifest,
        {"schema", "version", "source_release_candidate", "database", "rollback", "release_policy"},
        "stable manifest",
    )
    if manifest.get("schema") != EXPECTED_SCHEMA:
        raise StableReleaseError("unexpected stable manifest schema")

    rc = manifest.get("source_release_candidate")
    if not isinstance(rc, dict):
        raise StableReleaseError("source release candidate must be an object")
    _closed_keys(
        rc,
        {"version", "revision", "image", "rise2_validation", "rollback_seconds"},
        "source release candidate",
    )
    if rc != {
        "version": EXPECTED_RC_VERSION,
        "revision": EXPECTED_RC_REVISION,
        "image": EXPECTED_RC_IMAGE,
        "rise2_validation": "passed",
        "rollback_seconds": 15,
    }:
        raise StableReleaseError("stable release is not anchored to the validated RC evidence")

    database = manifest.get("database")
    if not isinstance(database, dict):
        raise StableReleaseError("database policy must be an object")
    _closed_keys(
        database,
        {"release_candidate_revision", "stable_revision", "versions_tree_sha"},
        "database policy",
    )
    if database != {
        "release_candidate_revision": EXPECTED_DATABASE_REVISION,
        "stable_revision": EXPECTED_DATABASE_REVISION,
        "versions_tree_sha": EXPECTED_MIGRATION_TREE,
    }:
        raise StableReleaseError("stable release must remain schema-neutral relative to the RC")

    rollback = manifest.get("rollback")
    if not isinstance(rollback, dict):
        raise StableReleaseError("rollback policy must be an object")
    _closed_keys(
        rollback,
        {"release_candidate_image", "previous_pilot_image", "v1_must_remain_available"},
        "rollback policy",
    )
    if rollback.get("release_candidate_image") != EXPECTED_RC_IMAGE:
        raise StableReleaseError("RC rollback image is not the validated RC digest")
    if rollback.get("previous_pilot_image") != EXPECTED_PREVIOUS_IMAGE:
        raise StableReleaseError("previous pilot rollback image changed")
    if rollback.get("v1_must_remain_available") is not True:
        raise StableReleaseError("V1 rollback window must remain available")

    if manifest.get("release_policy") != EXPECTED_POLICY:
        raise StableReleaseError("stable release policy crosses the V2-35 approval boundary")


def _validate_database(root: Path) -> None:
    tree = _run(root, "git", "rev-parse", "HEAD:backend/migrations/versions")
    if not re.fullmatch(r"[0-9a-f]{40}", tree) or tree != EXPECTED_MIGRATION_TREE:
        raise StableReleaseError("migration tree differs from the validated RC")


def _validate_workflows(root: Path) -> None:
    release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    deploy = (root / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    v2_image = (root / ".github/workflows/v2-image.yml").read_text(encoding="utf-8")
    v2_gate = (root / ".github/workflows/v2-rc.yml").read_text(encoding="utf-8")

    if "develop_V2" in release or "develop_V2" in deploy:
        raise StableReleaseError("V1 release/deploy workflows must remain isolated from V2")
    if "world-of-seeds-v2:sha-" not in v2_image:
        raise StableReleaseError("V2 integration image must remain immutable-SHA addressed")
    if "world-of-seeds-v2:2.0.0" in v2_image or "world-of-seeds-v2:latest" in v2_image:
        raise StableReleaseError("merge must not automatically publish a stable or latest tag")
    if "validate_v2_stable_release.py" not in v2_gate:
        raise StableReleaseError("develop_V2 qualification does not validate V2 stable releases")


def _validate_runbook(root: Path) -> None:
    try:
        runbook = (root / RUNBOOK_PATH).read_text(encoding="utf-8")
    except OSError as exc:
        raise StableReleaseError("V2 stable runbook is unavailable") from exc
    required = (
        "## Stable artifact",
        "## Progressive Rise2 cutover",
        "## Rollback window",
        "## DNS and V1 boundary",
        EXPECTED_RC_REVISION,
        EXPECTED_RC_IMAGE,
        EXPECTED_PREVIOUS_IMAGE,
        "2.0.0",
        "explicit approval",
    )
    if any(marker not in runbook for marker in required):
        raise StableReleaseError("V2 stable runbook is incomplete")


def validate_stable_release(root: Path = ROOT) -> str:
    manifest = _load_json(root / MANIFEST_PATH)
    _validate_manifest(manifest)
    _validate_provenance(root)
    _validate_version(root, manifest)
    _validate_database(root)
    _validate_workflows(root)
    _validate_runbook(root)
    return EXPECTED_VERSION


def main() -> int:
    try:
        version = validate_stable_release()
    except (OSError, StableReleaseError) as exc:
        print(f"V2 stable release validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"V2 stable release policy: PASS ({version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
