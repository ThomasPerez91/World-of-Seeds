#!/usr/bin/env python3
"""Validate or update every application-version mirror from VERSION."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import tomllib

SEMVER_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class VersioningError(RuntimeError):
    """Raised when a version source cannot be validated or updated safely."""


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VersioningError(f"Cannot read {path}") from exc


def _match_one(path: Path, pattern: str) -> str:
    matches = re.findall(pattern, _read_text(path), flags=re.MULTILINE)
    if len(matches) != 1:
        raise VersioningError(
            f"Expected one version declaration in {path}, found {len(matches)}"
        )
    value = matches[0]
    if not isinstance(value, str):
        raise VersioningError(f"Invalid version declaration in {path}")
    return value


def canonical_version(root: Path) -> str:
    version = _read_text(root / "VERSION").strip()
    if SEMVER_PATTERN.fullmatch(version) is None:
        raise VersioningError(f"VERSION is not a stable semantic version: {version!r}")
    return version


def version_sources(root: Path) -> dict[str, str]:
    backend_project = tomllib.loads(_read_text(root / "backend/pyproject.toml"))
    backend_lock = tomllib.loads(_read_text(root / "backend/uv.lock"))
    backend_packages = [
        package
        for package in backend_lock.get("package", [])
        if package.get("name") == "world-of-seeds-backend"
    ]
    if len(backend_packages) != 1:
        raise VersioningError(
            "backend/uv.lock must contain exactly one application package"
        )

    frontend_package = json.loads(_read_text(root / "frontend/package.json"))
    frontend_lock = json.loads(_read_text(root / "frontend/package-lock.json"))

    return {
        "VERSION": canonical_version(root),
        "backend/pyproject.toml": str(backend_project["project"]["version"]),
        "backend/app/__init__.py": _match_one(
            root / "backend/app/__init__.py",
            r'^__version__ = "([^"]+)"$',
        ),
        "backend/uv.lock": str(backend_packages[0]["version"]),
        "frontend/package.json": str(frontend_package["version"]),
        "frontend/package-lock.json": str(frontend_lock["version"]),
        "frontend/package-lock.json root package": str(
            frontend_lock["packages"][""]["version"]
        ),
        "frontend/src/version.ts": _match_one(
            root / "frontend/src/version.ts",
            r'^export const APP_VERSION = "([^"]+)";$',
        ),
    }


def validate(
    root: Path,
    *,
    expected_version: str | None = None,
    expected_tag: str | None = None,
) -> str:
    sources = version_sources(root)
    version = sources["VERSION"]
    mismatches = {name: value for name, value in sources.items() if value != version}
    if mismatches:
        details = ", ".join(
            f"{name}={value}" for name, value in sorted(mismatches.items())
        )
        raise VersioningError(
            f"Version mirrors differ from VERSION={version}: {details}"
        )
    if expected_version is not None and expected_version != version:
        raise VersioningError(
            f"Expected application version {expected_version}, found {version}"
        )
    if expected_tag is not None and expected_tag != f"v{version}":
        raise VersioningError(f"Tag {expected_tag} does not match VERSION={version}")
    return version


def _replace_one(path: Path, pattern: str, replacement: str) -> None:
    source = _read_text(path)
    updated, count = re.subn(pattern, replacement, source, count=1, flags=re.MULTILINE)
    if count != 1:
        raise VersioningError(
            f"Refusing to update ambiguous version declaration in {path}"
        )
    path.write_text(updated, encoding="utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def set_version(root: Path, version: str) -> None:
    if SEMVER_PATTERN.fullmatch(version) is None:
        raise VersioningError(f"Invalid stable semantic version: {version!r}")

    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    _replace_one(
        root / "backend/pyproject.toml",
        r'(^\[project\]\nname = "world-of-seeds-backend"\nversion = ")[^"]+("$)',
        rf"\g<1>{version}\g<2>",
    )
    _replace_one(
        root / "backend/app/__init__.py",
        r'(^__version__ = ")[^"]+("$)',
        rf"\g<1>{version}\g<2>",
    )
    _replace_one(
        root / "backend/uv.lock",
        r'(\[\[package\]\]\nname = "world-of-seeds-backend"\nversion = ")[^"]+("$)',
        rf"\g<1>{version}\g<2>",
    )

    frontend_package_path = root / "frontend/package.json"
    frontend_package = json.loads(_read_text(frontend_package_path))
    frontend_package["version"] = version
    _write_json(frontend_package_path, frontend_package)

    frontend_lock_path = root / "frontend/package-lock.json"
    frontend_lock = json.loads(_read_text(frontend_lock_path))
    frontend_lock["version"] = version
    frontend_lock["packages"][""]["version"] = version
    _write_json(frontend_lock_path, frontend_lock)

    _replace_one(
        root / "frontend/src/version.ts",
        r'(^export const APP_VERSION = ")[^"]+((";)$)',
        rf"\g<1>{version}\g<2>",
    )
    validate(root, expected_version=version)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the script parent)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="validate every version mirror")
    check.add_argument("--expected-version")
    check.add_argument("--expected-tag")
    check.add_argument("--print-version", action="store_true")

    update = subparsers.add_parser("set", help="update every version mirror")
    update.add_argument("version")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    root = arguments.root.resolve()
    try:
        if arguments.command == "set":
            set_version(root, arguments.version)
            print(arguments.version)
        else:
            version = validate(
                root,
                expected_version=arguments.expected_version,
                expected_tag=arguments.expected_tag,
            )
            if arguments.print_version:
                print(version)
    except (
        KeyError,
        TypeError,
        ValueError,
        tomllib.TOMLDecodeError,
        VersioningError,
    ) as exc:
        print(f"Version validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
