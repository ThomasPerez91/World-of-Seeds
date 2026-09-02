#!/usr/bin/env python3
"""Create, verify, and safely stage encrypted Rise2 V2 backups."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

SCHEMA_VERSION = 2
PROJECT_NAME = "world-of-seeds-v2-rise2"
REPOSITORY = Path(__file__).resolve().parents[1]
COMPOSE_FILE = REPOSITORY / "deploy/compose.rise2.v2.yaml"
PAUSABLE_SERVICES = ("worker", "scheduler", "qbittorrent", "newgreedy")
REQUIRED_ENV = {
    "WOS_V2_STORAGE_HOST_PATH",
    "WOS_V2_POSTGRES_DB",
    "WOS_V2_POSTGRES_USER",
    "WOS_V2_QBITTORRENT_CONFIG_PATH",
    "WOS_V2_NEWGREEDY_CONFIG_PATH",
    "WOS_V2_NEWGREEDY_STATE_HOST_PATH",
}
PRIVATE_MODES = {
    "environment": 0o600,
    "qBittorrent.conf": 0o600,
    "newgreedy/config.ini": 0o640,
    "postgres.dump": 0o600,
}
COMPONENTS = {
    "postgresql": "postgres.dump",
    "environment": "environment",
    "qbittorrent_bootstrap": "qBittorrent.conf",
    "qbittorrent_state": "qbittorrent-config",
    "newgreedy_config": "newgreedy/config.ini",
    "newgreedy_state": "newgreedy-state",
    "newgreedy_ca": "newgreedy-ca",
}


class BackupError(RuntimeError):
    """A backup invariant or command failed without exposing secret values."""


def parse_environment(path: Path) -> dict[str, str]:
    """Parse the restricted KEY=VALUE environment format used by Rise2."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BackupError(f"cannot read environment file: {path}") from exc
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or "=" not in line:
            raise BackupError(f"invalid environment assignment on line {number}")
        key, value = line.split("=", 1)
        if not key or not key.replace("_", "a").isalnum() or not key[0].isalpha():
            raise BackupError(f"invalid environment key on line {number}")
        if key in values:
            raise BackupError(f"duplicate environment key on line {number}")
        values[key] = value
    missing = sorted(REQUIRED_ENV - values.keys())
    if missing:
        raise BackupError(f"environment is missing required keys: {', '.join(missing)}")
    return values


def _resolved_file(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BackupError(f"{label} does not exist: {path}") from exc
    if not resolved.is_file():
        raise BackupError(f"{label} must be a regular file: {path}")
    if path.is_symlink():
        raise BackupError(f"{label} must not be a symbolic link: {path}")
    return resolved


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_environment_paths(
    environment_path: Path, values: Mapping[str, str]
) -> dict[str, Path]:
    """Resolve V2-only source paths and reject any V1 or out-of-scope path."""
    environment = _resolved_file(environment_path, "environment file")
    storage = Path(values["WOS_V2_STORAGE_HOST_PATH"]).resolve(strict=True)
    if not storage.is_dir() or not _is_relative_to(storage, Path("/srv/world-of-seeds-v2")):
        raise BackupError("storage must be a real directory below /srv/world-of-seeds-v2")
    config_root = Path("/etc/world-of-seeds-v2")
    qbittorrent = _resolved_file(
        Path(values["WOS_V2_QBITTORRENT_CONFIG_PATH"]), "qBittorrent bootstrap config"
    )
    newgreedy = _resolved_file(Path(values["WOS_V2_NEWGREEDY_CONFIG_PATH"]), "NewGreedy config")
    newgreedy_state = Path(values["WOS_V2_NEWGREEDY_STATE_HOST_PATH"])
    try:
        resolved_newgreedy_state = newgreedy_state.resolve(strict=True)
    except OSError as exc:
        raise BackupError("NewGreedy state directory does not exist") from exc
    if (
        newgreedy_state.is_symlink()
        or not resolved_newgreedy_state.is_dir()
        or not _is_relative_to(resolved_newgreedy_state, Path("/srv/world-of-seeds-v2"))
    ):
        raise BackupError("NewGreedy state must be a real V2 directory")
    for path, label in (
        (environment, "environment"),
        (qbittorrent, "qBittorrent"),
        (newgreedy, "NewGreedy"),
    ):
        if not _is_relative_to(path, config_root):
            raise BackupError(f"{label} config must remain below /etc/world-of-seeds-v2")
    for path, mode, label in (
        (environment, 0o600, "environment"),
        (qbittorrent, 0o600, "qBittorrent"),
        (newgreedy, 0o640, "NewGreedy"),
    ):
        if path.stat().st_mode & 0o777 != mode:
            raise BackupError(f"{label} config must use mode {mode:04o}")
    return {
        "environment": environment,
        "storage": storage,
        "qbittorrent": qbittorrent,
        "newgreedy": newgreedy,
        "newgreedy_state": resolved_newgreedy_state,
    }


def validate_backup_target(output: Path, storage: Path) -> tuple[Path, Path]:
    parent = output.parent.resolve(strict=True)
    target = parent / output.name
    checksum = target.with_name(target.name + ".sha256")
    if target.suffixes[-2:] != [".tar", ".age"]:
        raise BackupError("backup output must end with .tar.age")
    if target.exists() or checksum.exists():
        raise BackupError("backup output and checksum sidecar must not already exist")
    if _is_relative_to(target, storage) or _is_relative_to(target, Path("/srv/seedbox")):
        raise BackupError("backup output must be outside V1 and V2 content storage")
    return target, checksum


def _compose(environment: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(environment),
        "--file",
        str(COMPOSE_FILE),
        *arguments,
    ]


def _run(
    command: Sequence[str],
    *,
    label: str,
    stdout: BinaryIO | int | None = subprocess.PIPE,
    stdin: BinaryIO | int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            command,
            check=False,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise BackupError(f"{label} could not start") from exc
    if result.returncode != 0:
        raise BackupError(f"{label} failed with exit code {result.returncode}")
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BackupError(f"backup source contains a symbolic link: {path.relative_to(root)}")
        if path.is_file():
            yield path
        elif not path.is_dir():
            raise BackupError(f"backup source contains a special file: {path.relative_to(root)}")


def _copy_private(source: Path, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, destination, follow_symlinks=False)
    destination.chmod(mode)


def _write_manifest(stage: Path, content_snapshot_id: str) -> None:
    files = {
        path.relative_to(stage).as_posix(): _sha256(path)
        for path in _regular_files(stage)
        if path.name != "manifest.json"
    }
    manifest = {
        "schema": SCHEMA_VERSION,
        "project": PROJECT_NAME,
        "created_at": datetime.now(UTC).isoformat(),
        "content": {
            "policy": "external-snapshot-required",
            "snapshot_id": content_snapshot_id,
            "included_in_archive": False,
        },
        "components": COMPONENTS,
        "files": files,
    }
    path = stage / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _create_tar(stage: Path, destination: Path) -> None:
    with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(stage.rglob("*")):
            archive.add(path, arcname=path.relative_to(stage), recursive=False)
    destination.chmod(0o600)


def _capture_service_state(environment: Path) -> set[str]:
    result = _run(
        _compose(environment, "ps", "--services", "--status", "running"),
        label="Compose service-state query",
    )
    return set(result.stdout.decode("utf-8").splitlines())


def create_backup(
    environment_path: Path,
    output: Path,
    age_recipient: str,
    content_snapshot_id: str,
) -> Path:
    if shutil.which("age") is None:
        raise BackupError("age is required to create an encrypted backup")
    if not age_recipient.startswith(("age1", "age-plugin-")):
        raise BackupError("age recipient must be an age or age-plugin public recipient")
    if not content_snapshot_id.strip() or any(char.isspace() for char in content_snapshot_id):
        raise BackupError("content snapshot ID must be non-empty and contain no whitespace")
    values = parse_environment(environment_path)
    paths = validate_environment_paths(environment_path, values)
    target, checksum_path = validate_backup_target(output, paths["storage"])
    running = _capture_service_state(paths["environment"])
    not_quiesced = sorted(set(PAUSABLE_SERVICES) & running)
    if not_quiesced:
        raise BackupError(
            "content consumers must be stopped before the external snapshot and backup: "
            + ", ".join(not_quiesced)
        )

    with tempfile.TemporaryDirectory(prefix="wos-v2-backup-") as raw_temp:
        temporary = Path(raw_temp)
        temporary.chmod(0o700)
        stage = temporary / "payload"
        stage.mkdir(mode=0o700)

        _copy_private(paths["environment"], stage / "environment", PRIVATE_MODES["environment"])
        _copy_private(
            paths["qbittorrent"],
            stage / "qBittorrent.conf",
            PRIVATE_MODES["qBittorrent.conf"],
        )
        _copy_private(
            paths["newgreedy"],
            stage / "newgreedy/config.ini",
            PRIVATE_MODES["newgreedy/config.ini"],
        )
        with (stage / "postgres.dump").open("wb") as dump:
            _run(
                _compose(
                    paths["environment"],
                    "exec",
                    "-T",
                    "postgres",
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--username",
                    values["WOS_V2_POSTGRES_USER"],
                    "--dbname",
                    values["WOS_V2_POSTGRES_DB"],
                ),
                label="PostgreSQL consistent dump",
                stdout=dump,
            )
        (stage / "postgres.dump").chmod(PRIVATE_MODES["postgres.dump"])
        _run(
            _compose(
                paths["environment"],
                "cp",
                "qbittorrent:/config/.",
                str(stage / "qbittorrent-config"),
            ),
            label="qBittorrent state copy",
        )
        newgreedy_state = stage / "newgreedy-state"
        newgreedy_state.mkdir(mode=0o700)
        for filename in (
            "stats.json",
            "torrent_registry.json",
            "newgreedy.log",
            "purge_pending.json",
        ):
            _run(
                _compose(
                    paths["environment"],
                    "cp",
                    f"newgreedy:/app/{filename}",
                    str(newgreedy_state / filename),
                ),
                label=f"NewGreedy {filename} copy",
            )
        _run(
            _compose(
                paths["environment"],
                "cp",
                "newgreedy:/root/.mitmproxy/.",
                str(stage / "newgreedy-ca"),
            ),
            label="NewGreedy CA copy",
        )
        _write_manifest(stage, content_snapshot_id)
        tar_path = temporary / "backup.tar"
        _create_tar(stage, tar_path)
        try:
            _run(
                [
                    "age",
                    "--recipient",
                    age_recipient,
                    "--output",
                    str(target),
                    str(tar_path),
                ],
                label="age encryption",
            )
        except BackupError:
            target.unlink(missing_ok=True)
            raise
        target.chmod(0o600)
        checksum = _sha256(target)
        checksum_path.write_text(f"{checksum}  {target.name}\n", encoding="ascii")
        checksum_path.chmod(0o600)
    return target


def verify_checksum(backup: Path) -> None:
    backup = _resolved_file(backup, "encrypted backup")
    checksum_path = _resolved_file(
        backup.with_name(backup.name + ".sha256"), "backup checksum sidecar"
    )
    fields = checksum_path.read_text(encoding="ascii").strip().split()
    if len(fields) != 2 or fields[1] != backup.name or len(fields[0]) != 64:
        raise BackupError("backup checksum sidecar has an invalid format")
    if not hmac.compare_digest(fields[0], _sha256(backup)):
        raise BackupError("encrypted backup checksum does not match")


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r") as bundle:
        members = bundle.getmembers()
        for member in members:
            member_path = PurePosixPath(member.name)
            if (
                member_path.is_absolute()
                or ".." in member_path.parts
                or member.issym()
                or member.islnk()
                or member.isdev()
                or member.isfifo()
            ):
                raise BackupError("backup archive contains an unsafe entry")
        bundle.extractall(destination, members=members, filter="data")


def validate_payload(stage: Path, expected_snapshot_id: str | None = None) -> dict[str, Any]:
    manifest_path = _resolved_file(stage / "manifest.json", "backup manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise BackupError("backup manifest must be an object")
    if manifest.get("schema") != SCHEMA_VERSION or manifest.get("project") != PROJECT_NAME:
        raise BackupError("backup manifest schema or project does not match Rise2 V2")
    content = manifest.get("content")
    if not isinstance(content, dict) or content.get("policy") != "external-snapshot-required":
        raise BackupError("backup manifest has no explicit external content policy")
    if content.get("included_in_archive") is not False:
        raise BackupError("backup manifest content policy is inconsistent")
    if expected_snapshot_id is not None and content.get("snapshot_id") != expected_snapshot_id:
        raise BackupError("external content snapshot ID does not match the backup")
    declared = manifest.get("files")
    if not isinstance(declared, dict) or not declared:
        raise BackupError("backup manifest contains no file checksums")
    if manifest.get("components") != COMPONENTS:
        raise BackupError("backup manifest component map is incomplete")
    for relative in ("qbittorrent-config", "newgreedy-state", "newgreedy-ca"):
        if not (stage / relative).is_dir() or (stage / relative).is_symlink():
            raise BackupError(f"backup payload component is missing: {relative}")
    for relative in (
        "newgreedy-state/stats.json",
        "newgreedy-state/torrent_registry.json",
        "newgreedy-state/newgreedy.log",
        "newgreedy-state/purge_pending.json",
        "newgreedy-ca/mitmproxy-ca-cert.pem",
        "newgreedy-ca/mitmproxy-ca.pem",
    ):
        _resolved_file(stage / relative, f"backup payload {relative}")
    postgres_dump = _resolved_file(stage / "postgres.dump", "PostgreSQL dump")
    with postgres_dump.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise BackupError("PostgreSQL dump is not in custom format")
    actual = {
        path.relative_to(stage).as_posix(): _sha256(path)
        for path in _regular_files(stage)
        if path.relative_to(stage).as_posix() != "manifest.json"
    }
    if set(declared) != set(actual):
        raise BackupError("backup payload file list does not match its manifest")
    for relative, digest in actual.items():
        if not isinstance(declared[relative], str) or not hmac.compare_digest(
            declared[relative], digest
        ):
            raise BackupError(f"backup payload checksum mismatch: {relative}")
    return manifest


def restore_backup(
    backup: Path,
    identity: Path,
    target: Path,
    content_snapshot_id: str,
) -> Path:
    verify_checksum(backup)
    backup = backup.resolve(strict=True)
    identity = _resolved_file(identity, "age identity")
    parent = target.parent.resolve(strict=True)
    target = parent / target.name
    if target.exists():
        raise BackupError("restore target must not already exist")
    forbidden_roots = (
        Path("/srv/seedbox"),
        Path("/srv/world-of-seeds-v2"),
        Path("/etc/world-of-seeds-v2"),
    )
    if any(_is_relative_to(target, root) for root in forbidden_roots):
        raise BackupError("restore target must be outside V1 and live V2 data/config trees")

    with tempfile.TemporaryDirectory(prefix=".wos-v2-restore-", dir=parent) as raw_temp:
        temporary = Path(raw_temp)
        temporary.chmod(0o700)
        tar_path = temporary / "backup.tar"
        _run(
            [
                "age",
                "--decrypt",
                "--identity",
                str(identity),
                "--output",
                str(tar_path),
                str(backup),
            ],
            label="age decryption",
        )
        stage = temporary / "payload"
        stage.mkdir(mode=0o700)
        _safe_extract(tar_path, stage)
        validate_payload(stage, content_snapshot_id)
        for relative, mode in PRIVATE_MODES.items():
            _resolved_file(stage / relative, f"restored {relative}").chmod(mode)
        os.replace(stage, target)
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="create an encrypted backup")
    backup.add_argument("--env-file", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--age-recipient", required=True)
    backup.add_argument("--content-snapshot-id", required=True)

    verify = commands.add_parser("verify", help="verify the encrypted artifact checksum")
    verify.add_argument("backup", type=Path)

    restore = commands.add_parser("restore", help="decrypt and stage a verified backup")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--identity", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    restore.add_argument("--content-snapshot-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "backup":
            target = create_backup(
                args.env_file,
                args.output,
                args.age_recipient,
                args.content_snapshot_id,
            )
            print(f"encrypted backup created: {target}")
        elif args.command == "verify":
            verify_checksum(args.backup)
            print("encrypted backup checksum verified")
        else:
            target = restore_backup(
                args.backup,
                args.identity,
                args.target,
                args.content_snapshot_id,
            )
            print(f"verified backup staged in: {target}")
    except BackupError as exc:
        print(f"Rise2 V2 backup error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
