import io
import json
import runpy
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def _backup_namespace() -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[2]
    return runpy.run_path(str(repository / "scripts/rise2_v2_backup.py"))


def _environment(path: Path, *, storage: str = "/srv/world-of-seeds-v2/data") -> Path:
    path.write_text(
        "\n".join(
            (
                f"WOS_V2_STORAGE_HOST_PATH={storage}",
                "WOS_V2_POSTGRES_DB=wos_v2",
                "WOS_V2_POSTGRES_USER=wos_v2",
                "WOS_V2_QBITTORRENT_CONFIG_PATH=/etc/world-of-seeds-v2/qBittorrent.conf",
                "WOS_V2_NEWGREEDY_CONFIG_PATH=/etc/world-of-seeds-v2/newgreedy/config.ini",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _payload(root: Path, snapshot_id: str = "zfs-rise2-20260828T010000Z") -> Path:
    namespace = _backup_namespace()
    stage = root / "payload"
    (stage / "newgreedy").mkdir(parents=True)
    for relative, content in (
        ("environment", b"secret=value\n"),
        ("qBittorrent.conf", b"[Preferences]\n"),
        ("newgreedy/config.ini", b"[main]\n"),
        ("postgres.dump", b"PGDMP-test"),
    ):
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (stage / "qbittorrent-config").mkdir()
    (stage / "newgreedy-data").mkdir()
    namespace["_write_manifest"](stage, snapshot_id)
    return stage


def test_environment_parser_accepts_json_values_without_interpolation(tmp_path: Path) -> None:
    namespace = _backup_namespace()
    path = _environment(tmp_path / "environment")
    with path.open("a", encoding="utf-8") as stream:
        stream.write('WOS_V2_INTEGRATION_ACCOUNTS_JSON={"password":"$literal"}\n')

    values = namespace["parse_environment"](path)

    assert values["WOS_V2_INTEGRATION_ACCOUNTS_JSON"] == '{"password":"$literal"}'


@pytest.mark.parametrize(
    "line",
    (
        "export WOS_V2_POSTGRES_DB=wos_v2",
        "NOT_AN_ASSIGNMENT",
        "WOS_V2_POSTGRES_DB=duplicate",
    ),
)
def test_environment_parser_rejects_ambiguous_or_duplicate_lines(tmp_path: Path, line: str) -> None:
    namespace = _backup_namespace()
    path = _environment(tmp_path / "environment")
    with path.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")

    with pytest.raises(namespace["BackupError"]):
        namespace["parse_environment"](path)


def test_backup_target_rejects_content_trees_and_existing_outputs(tmp_path: Path) -> None:
    namespace = _backup_namespace()
    storage = tmp_path / "data"
    storage.mkdir()

    with pytest.raises(namespace["BackupError"]):
        namespace["validate_backup_target"](storage / "backup.tar.age", storage)

    existing = tmp_path / "backup.tar.age"
    existing.write_bytes(b"existing")
    with pytest.raises(namespace["BackupError"]):
        namespace["validate_backup_target"](existing, storage)


def test_manifest_validation_detects_tampering_and_unlisted_files(tmp_path: Path) -> None:
    namespace = _backup_namespace()
    stage = _payload(tmp_path)
    namespace["validate_payload"](stage, "zfs-rise2-20260828T010000Z")

    (stage / "postgres.dump").write_bytes(b"PGDMP-tampered")
    with pytest.raises(namespace["BackupError"], match="checksum mismatch"):
        namespace["validate_payload"](stage, "zfs-rise2-20260828T010000Z")

    (stage / "postgres.dump").write_bytes(b"PGDMP-test")
    (stage / "undeclared").write_bytes(b"extra")
    with pytest.raises(namespace["BackupError"], match="file list"):
        namespace["validate_payload"](stage, "zfs-rise2-20260828T010000Z")


def test_manifest_requires_matching_external_content_snapshot(tmp_path: Path) -> None:
    namespace = _backup_namespace()
    stage = _payload(tmp_path)

    with pytest.raises(namespace["BackupError"], match="snapshot ID"):
        namespace["validate_payload"](stage, "wrong-snapshot")


def test_manifest_rejects_missing_state_or_non_custom_postgres_dump(tmp_path: Path) -> None:
    namespace = _backup_namespace()
    stage = _payload(tmp_path)
    (stage / "newgreedy-data").rmdir()
    with pytest.raises(namespace["BackupError"], match="component is missing"):
        namespace["validate_payload"](stage, "zfs-rise2-20260828T010000Z")

    (stage / "newgreedy-data").mkdir()
    (stage / "postgres.dump").write_bytes(b"plain SQL")
    namespace["_write_manifest"](stage, "zfs-rise2-20260828T010000Z")
    with pytest.raises(namespace["BackupError"], match="custom format"):
        namespace["validate_payload"](stage, "zfs-rise2-20260828T010000Z")


def test_safe_extract_rejects_path_traversal_and_links(tmp_path: Path) -> None:
    namespace = _backup_namespace()
    extractor: Callable[[Path, Path], None] = namespace["_safe_extract"]
    for name, linkname in (("../escape", ""), ("link", "/etc/passwd")):
        archive = tmp_path / f"{name.replace('/', '-')}.tar"
        with tarfile.open(archive, "w") as bundle:
            entry = tarfile.TarInfo(name)
            if linkname:
                entry.type = tarfile.SYMTYPE
                entry.linkname = linkname
                bundle.addfile(entry)
            else:
                content = b"escape"
                entry.size = len(content)
                bundle.addfile(entry, io.BytesIO(content))
        with pytest.raises(namespace["BackupError"], match="unsafe entry"):
            extractor(archive, tmp_path / "restore")


def test_checksum_sidecar_binds_digest_and_filename(tmp_path: Path) -> None:
    namespace = _backup_namespace()
    backup = tmp_path / "rise2.tar.age"
    backup.write_bytes(b"ciphertext")
    digest = namespace["_sha256"](backup)
    sidecar = tmp_path / "rise2.tar.age.sha256"
    sidecar.write_text(f"{digest}  {backup.name}\n", encoding="ascii")

    namespace["verify_checksum"](backup)
    sidecar.write_text(f"{digest}  another.tar.age\n", encoding="ascii")
    with pytest.raises(namespace["BackupError"], match="invalid format"):
        namespace["verify_checksum"](backup)


def test_manifest_contains_no_secret_values_or_content_names(tmp_path: Path) -> None:
    stage = _payload(tmp_path)
    manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))

    rendered = json.dumps(manifest)
    assert "secret=value" not in rendered
    assert manifest["content"] == {
        "included_in_archive": False,
        "policy": "external-snapshot-required",
        "snapshot_id": "zfs-rise2-20260828T010000Z",
    }


def test_restore_drill_is_disposable_isolated_and_never_reads_v1() -> None:
    repository = Path(__file__).resolve().parents[2]
    script = (repository / "scripts/rise2_v2_restore_drill.sh").read_text(encoding="utf-8")

    assert "--network none" in script
    assert "postgres:17.11-alpine3.24" in script
    assert 'docker volume rm "$drill_volume"' in script
    assert "volume_created=false" in script
    assert 'if [ "$volume_created" = true ]' in script
    assert "/srv/seedbox" not in script
    assert 'cat "$restore_dir/environment"' not in script
    assert '"secrets_included": False' in script
