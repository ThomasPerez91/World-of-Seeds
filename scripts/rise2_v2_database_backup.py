#!/usr/bin/env python3
"""Online encrypted PostgreSQL backups with a disposable restore check."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

NAME = re.compile(r"wos-db-(\d{8}T\d{6}Z)-[0-9a-f]{12}\.dump\.age")
ROOT = Path("/var/backups/world-of-seeds-v2/postgres")
METRICS = Path(
    "/var/lib/world-of-seeds-v2/node-exporter-textfile/wos_database_backup.prom"
)
ESSENTIAL = ("alembic_version", "users", "managed_torrents", "torrent_requests")


class BackupError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    source: BinaryIO | None = None,
    target: BinaryIO | None = None,
    timeout: int = 900,
) -> str:
    try:
        result = subprocess.run(
            args,
            stdin=source,
            stdout=target or subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupError("External command unavailable or timed out") from exc
    if result.returncode:
        raise BackupError("External command failed")  # Never expose SQL/keys/stderr.
    return result.stdout.decode().strip() if target is None else ""


def private_directory(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise BackupError("Backup directory must be an absolute non-symlink path")
    if any(
        path.is_relative_to(Path(p))
        for p in ("/srv/seedbox", "/srv/world-of-seeds-v2", "/etc", "/opt")
    ):
        raise BackupError("Backup directory overlaps a protected tree")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise BackupError("Backup directory must be private (0700)")
    return path


def atomic(path: Path, content: str, mode: int = 0o600) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            stream.write(content.encode())
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temp, path)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            temp.unlink(missing_ok=True)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def restore_dump(dump: Path, image: str) -> dict[str, object]:
    """Write exclusively into a fresh container/volume with no network or host port."""
    if not re.fullmatch(
        r"(?:sha256:[0-9a-f]{64}|postgres:[A-Za-z0-9._-]+|postgres@sha256:[0-9a-f]{64})",
        image,
    ):
        raise BackupError("Use an image ID/digest or an explicit PostgreSQL tag")
    name = "wos-db-restore-" + uuid.uuid4().hex
    volume_created = container_created = False
    started = time.monotonic()
    try:
        run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                "org.worldofseeds.database-restore=" + name,
                name,
            ]
        )
        volume_created = True
        run(
            [
                "docker",
                "create",
                "--name",
                name,
                "--network",
                "none",
                "--security-opt",
                "no-new-privileges:true",
                "--cpus",
                "1",
                "--memory",
                "1g",
                "--pids-limit",
                "128",
                "--volume",
                name + ":/var/lib/postgresql/data",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "--env",
                "POSTGRES_USER=wos_restore",
                "--env",
                "POSTGRES_DB=wos_restore",
                image,
            ]
        )
        container_created = True
        run(["docker", "start", name])
        for _ in range(60):
            try:
                run(
                    [
                        "docker",
                        "exec",
                        name,
                        "pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        "wos_restore",
                        "-d",
                        "wos_restore",
                    ],
                    timeout=10,
                )
                break
            except BackupError:
                time.sleep(1)
        else:
            raise BackupError("Isolated PostgreSQL readiness timeout")
        with dump.open("rb") as stream:
            run(
                [
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "-U",
                    "wos_restore",
                    "-d",
                    "wos_restore",
                ],
                source=stream,
            )
        sql = (
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename IN ("
            + ",".join("'" + t + "'" for t in ESSENTIAL)
            + ")"
        )
        count = run(
            [
                "docker",
                "exec",
                name,
                "psql",
                "-U",
                "wos_restore",
                "-d",
                "wos_restore",
                "-At",
                "-c",
                sql,
            ]
        )
        if count != str(len(ESSENTIAL)):
            raise BackupError("Restored database is missing essential WoS tables")
        revision = run(
            [
                "docker",
                "exec",
                name,
                "psql",
                "-U",
                "wos_restore",
                "-d",
                "wos_restore",
                "-At",
                "-c",
                "SELECT version_num FROM alembic_version",
            ]
        )
        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", revision):
            raise BackupError("Restored migration revision is invalid")
        return {
            "result": "pass",
            "migration_revision": revision,
            "essential_table_count": len(ESSENTIAL),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        if container_created:
            run(["docker", "rm", "--force", name], timeout=60)
        if volume_created:
            owner = run(
                [
                    "docker",
                    "volume",
                    "inspect",
                    "--format",
                    '{{index .Labels "org.worldofseeds.database-restore"}}',
                    name,
                ],
                timeout=30,
            )
            if owner != name:
                raise BackupError("Restore volume ownership mismatch")
            run(["docker", "volume", "rm", name], timeout=60)


def verified_metadata(archive: Path) -> dict[str, object]:
    if (
        not NAME.fullmatch(archive.name)
        or archive.is_symlink()
        or not archive.is_file()
    ):
        raise BackupError("Invalid database archive")
    manifest = archive.with_name(archive.name + ".json")
    if (
        manifest.is_symlink()
        or not manifest.is_file()
        or manifest.stat().st_size > 8192
    ):
        raise BackupError("Missing or invalid archive manifest")
    metadata = json.loads(manifest.read_text())
    if (
        metadata.get("schema") != 1
        or metadata.get("archive") != archive.name
        or metadata.get("sha256") != digest(archive)
        or metadata.get("restore", {}).get("result") != "pass"
    ):
        raise BackupError("Archive integrity or restore proof is invalid")
    return metadata


def retention(root: Path, now: datetime) -> None:
    """Keep all points for 14 days plus newest point in eight ISO weeks."""
    candidates: list[tuple[datetime, Path]] = []
    for path in root.iterdir():
        match = NAME.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        try:
            stamp = datetime.strptime(match[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            verified_metadata(path)
        except (BackupError, ValueError, OSError, TypeError, AttributeError):
            continue  # Leave incomplete/unknown artifacts to OPS; never recursive deletion.
        candidates.append((stamp, path))
    weeks: set[tuple[int, int]] = set()
    for stamp, path in sorted(candidates, reverse=True):
        week = (stamp.isocalendar().year, stamp.isocalendar().week)
        keep_weekly = week not in weeks and len(weeks) < 8
        weeks.add(week)
        if stamp >= now - timedelta(days=14) or keep_weekly:
            continue
        path.unlink()
        path.with_name(path.name + ".json").unlink()


def publish_metrics(
    root: Path, metrics: Path, success: bool, last_success: int
) -> None:
    atomic(
        root / "status.json",
        json.dumps(
            {
                "last_success_timestamp": last_success,
                "last_attempt_timestamp": int(time.time()),
                "last_attempt_success": success,
            }
        )
        + "\n",
    )
    atomic(
        metrics,
        f"wos_database_backup_last_success_timestamp_seconds {last_success}\n"
        f"wos_database_backup_last_attempt_success {int(success)}\n",
        mode=0o644,
    )


def backup(
    root: Path, env_file: Path, repository: Path, recipient: str, metrics: Path
) -> Path:
    root = private_directory(root)
    with (root / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("Another backup is running") from exc
        last_success = 0
        status = root / "status.json"
        if status.is_file():
            last_success = int(
                json.loads(status.read_text()).get("last_success_timestamp", 0)
            )
        try:
            if not re.fullmatch(r"age1[0-9a-z]{58}", recipient):
                raise BackupError("A valid age public recipient is required")
            if (
                not env_file.is_file()
                or env_file.is_symlink()
                or env_file.stat().st_mode & 0o077
            ):
                raise BackupError("Environment must be a private regular file")
            if (
                metrics.name != "wos_database_backup.prom"
                or not metrics.parent.is_dir()
                or metrics.parent.is_symlink()
            ):
                raise BackupError("Backup textfile directory is missing or invalid")
            source = run(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    "world-of-seeds-v2-rise2",
                    "--env-file",
                    str(env_file),
                    "--file",
                    str(repository / "deploy/compose.rise2.v2.yaml"),
                    "ps",
                    "-q",
                    "postgres",
                ]
            )
            if not re.fullmatch(r"[0-9a-f]{12,64}", source):
                raise BackupError(
                    "Exactly one production PostgreSQL container is required"
                )
            image = run(["docker", "inspect", "--format", "{{.Image}}", source])
            size = run(
                [
                    "docker",
                    "exec",
                    source,
                    "sh",
                    "-ec",
                    'exec psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" -At -c "SELECT pg_database_size(current_database())"',
                ]
            )
            if (
                not size.isdigit()
                or shutil.disk_usage(root).free < int(size) * 2 + 1024**3
            ):
                raise BackupError("Insufficient free space for staging/encryption")

            now = datetime.now(UTC)
            name = f"wos-db-{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:12]}.dump.age"
            with tempfile.TemporaryDirectory(prefix=".stage-", dir=root) as directory:
                stage = Path(directory)
                dump = stage / "postgres.dump"
                with dump.open("wb") as output:
                    run(
                        [
                            "docker",
                            "exec",
                            source,
                            "sh",
                            "-ec",
                            'exec pg_dump --format=custom --no-owner --no-privileges --lock-wait-timeout=30s --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"',
                        ],
                        target=output,
                    )
                proof = restore_dump(dump, image)
                encrypted = stage / name
                run(
                    [
                        "age",
                        "--recipient",
                        recipient,
                        "--output",
                        str(encrypted),
                        str(dump),
                    ]
                )
                encrypted.chmod(0o600)
                archive = root / name
                metadata = {
                    "schema": 1,
                    "archive": name,
                    "sha256": digest(encrypted),
                    "created_at": now.isoformat(),
                    "postgres_image": image,
                    "restore": proof,
                }
                with encrypted.open("rb") as ciphertext:
                    os.fsync(ciphertext.fileno())
                os.link(
                    encrypted, archive
                )  # Atomic publish, never overwrite an existing point.
                encrypted.unlink()
                atomic(root / (name + ".json"), json.dumps(metadata, indent=2) + "\n")
            retention(root, now)
            publish_metrics(root, metrics, True, int(time.time()))
            return archive
        except BaseException:
            publish_metrics(root, metrics, False, last_success)
            raise


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("backup")
    create.add_argument("--directory", type=Path, default=ROOT)
    create.add_argument(
        "--env-file", type=Path, default=Path("/etc/world-of-seeds-v2/environment")
    )
    create.add_argument(
        "--repository", type=Path, default=Path("/opt/world-of-seeds-v2")
    )
    create.add_argument("--recipient-file", type=Path, required=True)
    create.add_argument("--metrics", type=Path, default=METRICS)
    restore = sub.add_parser("restore-check")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--identity", type=Path, required=True)
    restore.add_argument("--image", required=True)
    args = parser.parse_args()
    try:
        if args.command == "backup":
            archive = backup(
                args.directory,
                args.env_file,
                args.repository,
                args.recipient_file.read_text().strip(),
                args.metrics,
            )
            print(
                "Encrypted database backup and isolated restore passed: " + archive.name
            )
        else:
            verified_metadata(args.archive)
            if (
                not args.identity.is_file()
                or args.identity.is_symlink()
                or args.identity.stat().st_mode & 0o077
            ):
                raise BackupError("Restore identity must be a private regular file")
            with tempfile.TemporaryDirectory(prefix="wos-db-check-") as directory:
                dump = Path(directory) / "postgres.dump"
                run(
                    [
                        "age",
                        "--decrypt",
                        "--identity",
                        str(args.identity),
                        "--output",
                        str(dump),
                        str(args.archive),
                    ]
                )
                print(json.dumps(restore_dump(dump, args.image), sort_keys=True))
        return 0
    except BackupError as exc:
        print(f"Database backup/check failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, AttributeError):
        print(
            "Database backup/check failed; inspect prerequisites and private backup status.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
