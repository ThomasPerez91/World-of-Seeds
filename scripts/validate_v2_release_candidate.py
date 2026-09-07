#!/usr/bin/env python3
"""Validate the closed V2 release-candidate policy without deployment side effects."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path("deploy/v2-rc-manifest.json")
RUNBOOK_PATH = Path("docs/release-candidate-v2.md")
RC_VERSION_RE = re.compile(r"^2\.0\.0-rc\.[1-9][0-9]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
IMAGE_RE = re.compile(
    r"^ghcr\.io/thomasperez91/world-of-seeds-v2@sha256:[0-9a-f]{64}$"
)
REVISION_RE = re.compile(r"^[0-9]{8}_[0-9]+$")
EXPECTED_SCHEMA = "world-of-seeds-v2-release-candidate/v1"
EXPECTED_POLICY = {
    "automatic_deploy": False,
    "dns_switch": False,
    "v1_import": False,
    "stable_release": False,
    "v1_rollback_preserved": True,
}


class ReleaseCandidateError(RuntimeError):
    """A release-candidate invariant failed."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseCandidateError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseCandidateError(f"JSON root must be an object: {path}")
    return value


def _closed_keys(value: dict[str, Any], expected: set[str], description: str) -> None:
    if set(value) != expected:
        raise ReleaseCandidateError(f"{description} has unexpected keys")


def _validate_version(root: Path, manifest: dict[str, Any]) -> str:
    try:
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ReleaseCandidateError("VERSION is unavailable") from exc
    if not RC_VERSION_RE.fullmatch(version):
        raise ReleaseCandidateError("VERSION must be a 2.0.0 release candidate")
    if manifest.get("version") != version:
        raise ReleaseCandidateError("manifest version does not match VERSION")

    result = subprocess.run(
        [sys.executable, "scripts/versioning.py", "check", "--channel", "v2"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise ReleaseCandidateError("V2 version mirrors are inconsistent")
    return version


def _validate_manifest(manifest: dict[str, Any]) -> None:
    _closed_keys(
        manifest,
        {"schema", "version", "functional_freeze", "pilot", "database", "release_policy"},
        "release-candidate manifest",
    )
    if manifest.get("schema") != EXPECTED_SCHEMA:
        raise ReleaseCandidateError("unexpected release-candidate manifest schema")
    if manifest.get("functional_freeze") is not True:
        raise ReleaseCandidateError("functional freeze must be enabled")

    pilot = manifest.get("pilot")
    if not isinstance(pilot, dict):
        raise ReleaseCandidateError("pilot evidence must be an object")
    _closed_keys(
        pilot,
        {"approval_ref", "ledger_sha256", "runtime_revision", "previous_wos_image"},
        "pilot evidence",
    )
    if pilot.get("approval_ref") != "v2-33-go-20260907":
        raise ReleaseCandidateError("unexpected V2-33 approval reference")
    if not isinstance(pilot.get("ledger_sha256"), str) or not HEX64_RE.fullmatch(
        pilot["ledger_sha256"]
    ):
        raise ReleaseCandidateError("invalid pilot ledger digest")
    if not isinstance(pilot.get("runtime_revision"), str) or not SHA_RE.fullmatch(
        pilot["runtime_revision"]
    ):
        raise ReleaseCandidateError("invalid pilot runtime revision")
    if not isinstance(pilot.get("previous_wos_image"), str) or not IMAGE_RE.fullmatch(
        pilot["previous_wos_image"]
    ):
        raise ReleaseCandidateError("previous WOS image must be immutable")

    database = manifest.get("database")
    if not isinstance(database, dict):
        raise ReleaseCandidateError("database policy must be an object")
    _closed_keys(database, {"pilot_revision", "candidate_revision"}, "database policy")
    for key in ("pilot_revision", "candidate_revision"):
        if not isinstance(database.get(key), str) or not REVISION_RE.fullmatch(database[key]):
            raise ReleaseCandidateError(f"invalid database revision: {key}")
    if database["pilot_revision"] != database["candidate_revision"]:
        raise ReleaseCandidateError("V2-34 must remain schema-neutral relative to the pilot")

    release_policy = manifest.get("release_policy")
    if release_policy != EXPECTED_POLICY:
        raise ReleaseCandidateError("release policy would cross the V2-34 safety boundary")


def _validate_migration_head(root: Path, manifest: dict[str, Any]) -> None:
    revision = manifest["database"]["candidate_revision"]
    versions = sorted(
        path.name
        for path in (root / "backend/migrations/versions").glob("*.py")
        if path.name != "__init__.py"
    )
    matches = [name for name in versions if name.startswith(f"{revision}_")]
    if len(matches) != 1:
        raise ReleaseCandidateError("candidate migration revision is not unique")
    if not versions or matches[0] != versions[-1]:
        raise ReleaseCandidateError("a migration exists after the declared RC schema head")


def _validate_v1_release_isolation(root: Path) -> None:
    try:
        workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseCandidateError("stable release workflow is unavailable") from exc
    if "branches: [master]" not in workflow:
        raise ReleaseCandidateError("stable release workflow is no longer master-only")
    if "develop_V2" in workflow or "--channel v2" in workflow:
        raise ReleaseCandidateError("stable release workflow must remain isolated from V2")


def _validate_runbook(root: Path, manifest: dict[str, Any]) -> None:
    try:
        runbook = (root / RUNBOOK_PATH).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseCandidateError("V2 RC runbook is unavailable") from exc
    required = (
        "## Functional freeze",
        "## Candidate artifact",
        "## Database compatibility",
        "## Rise2 validation",
        "## Rollback",
        "## V2-35 boundary",
        manifest["pilot"]["previous_wos_image"],
        manifest["database"]["candidate_revision"],
    )
    if any(marker not in runbook for marker in required):
        raise ReleaseCandidateError("V2 RC runbook is incomplete")


def validate_release_candidate(root: Path = ROOT) -> str:
    manifest = _load_json(root / MANIFEST_PATH)
    _validate_manifest(manifest)
    version = _validate_version(root, manifest)
    _validate_migration_head(root, manifest)
    _validate_v1_release_isolation(root)
    _validate_runbook(root, manifest)
    return version


def main() -> int:
    try:
        version = validate_release_candidate()
    except ReleaseCandidateError as exc:
        print(f"V2 release candidate validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"V2 release candidate policy: PASS ({version})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
