import importlib.util
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def module() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/rise2_v2_database_backup.py"
    spec = importlib.util.spec_from_file_location("database_backup", path)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def pair(loaded: ModuleType, root: Path, stamp: datetime, number: int) -> Path:
    archive = root / f"wos-db-{stamp:%Y%m%dT%H%M%SZ}-{number:012x}.dump.age"
    archive.write_bytes(b"encrypted fixture")
    loaded.atomic(
        archive.with_name(archive.name + ".json"),
        json.dumps(
            {
                "schema": 1,
                "archive": archive.name,
                "sha256": loaded.digest(archive),
                "restore": {"result": "pass"},
            }
        ),
    )
    return archive


def test_retention_keeps_recent_and_eight_weekly_points_without_touching_unknowns(
    tmp_path: Path,
) -> None:
    loaded = module()
    now = datetime(2026, 10, 6, tzinfo=UTC)
    paths = [pair(loaded, tmp_path, now - timedelta(days=i), i) for i in range(90)]
    unknown = tmp_path / "operator-backup.dump.age"
    unknown.write_bytes(b"keep")
    corrupt = paths[-1]
    corrupt.write_bytes(b"corrupted")
    link = tmp_path / "wos-db-20260101T000000Z-ffffffffffff.dump.age"
    link.symlink_to(unknown)
    loaded.retention(tmp_path, now)
    assert all(path.exists() for path in paths[:15])
    assert not paths[50].exists()  # Not the newest point in its ISO week.
    assert corrupt.exists() and unknown.exists() and link.is_symlink()
    remaining = [path for path in paths if path.exists() and path != corrupt]
    assert len({path.name[7:15] for path in remaining}) >= 20


def test_archive_tamper_and_manifest_symlink_are_rejected(tmp_path: Path) -> None:
    loaded = module()
    archive = pair(loaded, tmp_path, datetime.now(UTC), 0)
    assert loaded.verified_metadata(archive)["restore"]["result"] == "pass"
    archive.write_bytes(b"changed")
    with pytest.raises(loaded.BackupError):
        loaded.verified_metadata(archive)
    manifest = archive.with_name(archive.name + ".json")
    manifest.unlink()
    manifest.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(loaded.BackupError):
        loaded.verified_metadata(archive)


def test_failed_dump_preserves_previous_success_and_publishes_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = module()
    root = tmp_path / "backups"
    loaded.private_directory(root)
    loaded.atomic(root / "status.json", '{"last_success_timestamp": 123}\n')
    env = tmp_path / "env"
    env.write_text("private=value")
    env.chmod(0o600)
    metrics = tmp_path / "wos_database_backup.prom"

    def fail(*args: Any, **kwargs: Any) -> str:
        raise loaded.BackupError("simulated")

    monkeypatch.setattr(loaded, "run", fail)
    with pytest.raises(loaded.BackupError):
        loaded.backup(root, env, tmp_path, "age1" + "a" * 58, metrics)
    assert "last_attempt_success 0" in metrics.read_text()
    assert "last_success_timestamp_seconds 123" in metrics.read_text()
    assert not list(root.glob("*.dump.age"))


def test_private_directory_rejects_symlinks_and_live_storage(tmp_path: Path) -> None:
    loaded = module()
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(actual)
    actual.chmod(0o755)
    for path in (link, Path("/srv/world-of-seeds-v2/data/backups"), actual):
        with pytest.raises(loaded.BackupError):
            loaded.private_directory(path)


def test_restore_failure_cleans_only_disposable_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = module()
    dump = tmp_path / "dump"
    dump.write_bytes(b"PGDMP-invalid")
    calls: list[list[str]] = []
    owned = ""

    def fake(args: list[str], **kwargs: Any) -> str:
        nonlocal owned
        calls.append(args)
        if args[:3] == ["docker", "volume", "create"]:
            owned = args[-1]
        if "pg_restore" in args:
            raise loaded.BackupError("restore rejected")
        if args[:3] == ["docker", "volume", "inspect"]:
            return owned
        return ""

    monkeypatch.setattr(loaded, "run", fake)
    with pytest.raises(loaded.BackupError):
        loaded.restore_dump(dump, "sha256:" + "a" * 64)
    assert ["docker", "rm", "--force", owned] in calls
    assert ["docker", "volume", "rm", owned] in calls
    create = next(call for call in calls if call[:2] == ["docker", "create"])
    assert create[create.index("--network") + 1] == "none"
    assert "--publish" not in create and all("/srv/" not in item for item in create)


@pytest.mark.skipif(
    os.getenv("WOS_TEST_DATABASE_BACKUP_DOCKER") != "1",
    reason="Docker + age integration is opt-in in CI",
)
def test_real_encrypted_dump_restore_and_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loaded = module()
    run = loaded.run
    seed = "wos-db-test-" + uuid.uuid4().hex
    identity = tmp_path / "identity"
    run(["age-keygen", "-o", str(identity)])
    recipient = run(["age-keygen", "-y", str(identity)])
    env = tmp_path / "environment"
    env.write_text("secret=not-in-public-report\n")
    env.chmod(0o600)
    root = tmp_path / "backups"
    metrics = tmp_path / "wos_database_backup.prom"
    dump = tmp_path / "decrypted.dump"
    seed_created = False
    try:
        # A unique test container, never the production Compose project or its volumes.
        run(
            [
                "docker",
                "create",
                "--name",
                seed,
                "--network",
                "none",
                "--env",
                "POSTGRES_HOST_AUTH_METHOD=trust",
                "--env",
                "POSTGRES_USER=wos_restore",
                "--env",
                "POSTGRES_DB=wos_restore",
                "postgres:17-alpine",
            ]
        )
        seed_created = True
        run(["docker", "start", seed])
        import time

        for _ in range(60):
            try:
                run(
                    [
                        "docker",
                        "exec",
                        seed,
                        "pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        "wos_restore",
                        "-d",
                        "wos_restore",
                    ]
                )
                break
            except loaded.BackupError:
                time.sleep(1)
        sql = (
            "CREATE TABLE alembic_version(version_num varchar(64)); "
            "INSERT INTO alembic_version VALUES ('20260929_35'); "
            "CREATE TABLE users(id int, name text); "
            "INSERT INTO users VALUES (1,'private-user'); "
            "CREATE TABLE managed_torrents(id int); CREATE TABLE torrent_requests(id int);"
        )
        run(
            [
                "docker",
                "exec",
                seed,
                "psql",
                "-U",
                "wos_restore",
                "-d",
                "wos_restore",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                sql,
            ]
        )
        seed_id = run(["docker", "inspect", "--format", "{{.Id}}", seed])
        image = run(["docker", "inspect", "--format", "{{.Image}}", seed])

        def scoped_run(args: list[str], **kwargs: Any) -> str:
            if args[:2] == ["docker", "compose"]:
                return str(seed_id)
            return str(run(args, **kwargs))

        monkeypatch.setattr(loaded, "run", scoped_run)
        archive = loaded.backup(root, env, tmp_path, recipient, metrics)
        metadata = loaded.verified_metadata(archive)
        assert metadata["restore"]["result"] == "pass"
        assert archive.stat().st_mode & 0o777 == 0o600
        assert not list(root.glob(".stage-*"))
        assert "private-user" not in json.dumps(metadata) and "secret" not in metrics.read_text()
        run(["age", "--decrypt", "--identity", str(identity), "--output", str(dump), str(archive)])
        assert loaded.restore_dump(dump, image)["result"] == "pass"
        # Repeat the production shutdown-safe restore path on a deliberately bad dump.
        dump.write_bytes(b"PGDMP-corrupt")
        with pytest.raises(loaded.BackupError):
            loaded.restore_dump(dump, image)
        archive.write_bytes(b"corrupt ciphertext")
        with pytest.raises(loaded.BackupError):
            loaded.verified_metadata(archive)
        assert not run(["docker", "ps", "-aq", "--filter", "name=wos-db-restore-"])
        assert not run(
            ["docker", "volume", "ls", "-q", "--filter", "label=org.worldofseeds.database-restore"]
        )
    finally:
        if seed_created:
            run(["docker", "rm", "-fv", seed])
