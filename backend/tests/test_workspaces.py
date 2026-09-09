import os
from pathlib import Path

import pytest

import app.files.workspaces as workspace_module
from app.files import (
    WorkspaceAlreadyExistsError,
    WorkspaceCompensationError,
    WorkspaceManager,
    WorkspaceUnsafeEntryError,
)
from app.files.structure import WORKSPACE_STRUCTURE
from app.trash.filesystem import TRASH_DIRECTORY


def make_manager(tmp_path: Path) -> tuple[WorkspaceManager, Path]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    return WorkspaceManager(data_root), data_root


def test_versioned_workspace_structure_is_the_runtime_reference() -> None:
    assert WORKSPACE_STRUCTURE.schema_version == 1
    assert WORKSPACE_STRUCTURE.directories == ("downloads",)
    assert WORKSPACE_STRUCTURE.protected_directories == ("downloads",)
    assert WORKSPACE_STRUCTURE.retired_directories == ("watch",)
    assert WORKSPACE_STRUCTURE.trash_directory == ".trash"
    assert WORKSPACE_STRUCTURE.trash_directory == TRASH_DIRECTORY


def test_workspace_is_created_directly_and_renamed_without_touching_qbittorrent_paths(
    tmp_path: Path,
) -> None:
    manager, data_root = make_manager(tmp_path)
    (data_root / "downloads").mkdir()
    (data_root / "watch").mkdir()
    legacy_marker = data_root / "downloads" / "still-seeding.mkv"
    legacy_marker.write_bytes(b"legacy")

    manager.create("guest-123abc")
    manager.rename("guest-123abc", "thomas")

    workspace = data_root / "thomas"
    assert {entry.name for entry in workspace.iterdir()} == {"downloads"}
    assert legacy_marker.read_bytes() == b"legacy"
    assert not (data_root / "guest-123abc").exists()


@pytest.mark.parametrize(
    "username",
    ["../../etc/passwd", "/tmp/outside", "alice/bob", "..", ".", "bad name", "bad\0name"],
)
def test_workspace_names_are_single_normalized_components(tmp_path: Path, username: str) -> None:
    manager, data_root = make_manager(tmp_path)

    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.create(username)

    assert list(data_root.iterdir()) == []


def test_workspace_symlink_is_never_adopted_or_renamed(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (data_root / "thomas").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceAlreadyExistsError):
        manager.create("thomas")
    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.assert_ready("thomas")
    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.rename("thomas", "renamed")

    assert list(outside.iterdir()) == []


def test_configured_data_root_symlink_is_rejected(tmp_path: Path) -> None:
    real_data_root = tmp_path / "real-data"
    real_data_root.mkdir()
    linked_data_root = tmp_path / "linked-data"
    linked_data_root.symlink_to(real_data_root, target_is_directory=True)
    manager = WorkspaceManager(linked_data_root)

    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.create("thomas")

    assert list(real_data_root.iterdir()) == []


def test_workspace_child_symlink_is_never_followed(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    manager.create("thomas")
    downloads = data_root / "thomas" / "downloads"
    downloads.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    downloads.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.assert_ready("thomas")
    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.remove_empty("thomas")

    assert downloads.is_symlink()
    assert list(outside.iterdir()) == []


def test_provision_is_removed_when_the_database_operation_fails(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)

    with (
        pytest.raises(RuntimeError, match="database failed"),
        manager.provision_for_transaction("thomas"),
    ):
        raise RuntimeError("database failed")

    assert not (data_root / "thomas").exists()


def test_rename_is_compensated_when_the_database_operation_fails(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    manager.create("temporary")
    marker = data_root / "temporary" / "downloads" / "video.mkv"
    marker.write_bytes(b"content")

    with (
        pytest.raises(RuntimeError, match="database failed"),
        manager.rename_for_transaction("temporary", "thomas"),
    ):
        raise RuntimeError("database failed")

    assert marker.read_bytes() == b"content"
    assert not (data_root / "thomas").exists()


def test_rename_never_replaces_a_concurrent_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, data_root = make_manager(tmp_path)
    manager.create("temporary")
    original_rename = workspace_module._rename_without_replacement

    def create_destination_then_rename(
        source: str,
        destination: str,
        *,
        source_directory_fd: int,
        destination_directory_fd: int,
    ) -> None:
        os.mkdir(destination, dir_fd=destination_directory_fd)
        original_rename(
            source,
            destination,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
        )

    monkeypatch.setattr(
        workspace_module,
        "_rename_without_replacement",
        create_destination_then_rename,
    )

    with pytest.raises(WorkspaceAlreadyExistsError):
        manager.rename("temporary", "thomas")

    assert (data_root / "temporary" / "downloads").is_dir()
    assert list((data_root / "thomas").iterdir()) == []


def test_compensation_never_deletes_a_non_empty_workspace(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    manager.create("thomas")
    marker = data_root / "thomas" / "downloads" / "video.mkv"
    marker.write_bytes(b"content")

    with pytest.raises(WorkspaceCompensationError):
        manager.remove_empty("thomas")

    assert marker.read_bytes() == b"content"


def test_uppercase_username_is_preserved_in_workspace_name(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)

    manager.create("Shadowsun")

    assert (data_root / "Shadowsun" / "downloads").is_dir()
    assert not (data_root / "shadowsun").exists()


def test_legacy_workspace_migration_is_atomic_idempotent_and_preserves_data(
    tmp_path: Path,
) -> None:
    manager, data_root = make_manager(tmp_path)
    legacy = data_root / "users" / "admin"
    (legacy / "downloads").mkdir(parents=True)
    (legacy / "watch").mkdir()
    marker = legacy / "downloads" / "video.mkv"
    marker.write_bytes(b"content")

    assert manager.migrate_legacy("admin") is True
    assert (data_root / "admin" / "downloads" / "video.mkv").read_bytes() == b"content"
    assert not legacy.exists()
    assert manager.migrate_legacy("admin") is False
    cleanup = manager.cleanup_retired_directories("admin")
    assert cleanup.removed == ("watch",)
    assert cleanup.retained == ()
    assert manager.remove_legacy_root_if_empty() is True
    assert not (data_root / "users").exists()


def test_legacy_migration_refuses_ambiguous_or_unsafe_states(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    manager.create("admin")
    legacy = data_root / "users" / "admin"
    (legacy / "downloads").mkdir(parents=True)
    (legacy / "watch").mkdir()

    with pytest.raises(WorkspaceAlreadyExistsError):
        manager.migrate_legacy("admin")

    assert (data_root / "admin" / "downloads").is_dir()
    assert (legacy / "downloads").is_dir()
    assert manager.remove_legacy_root_if_empty() is False


def test_retired_directory_cleanup_preserves_non_empty_data_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    manager, data_root = make_manager(tmp_path)
    manager.create("admin")
    watch = data_root / "admin" / "watch"
    watch.mkdir()
    marker = watch / "keep.torrent"
    marker.write_text("keep", encoding="utf-8")

    cleanup = manager.cleanup_retired_directories("admin")

    assert cleanup.removed == ()
    assert cleanup.retained == ("watch",)
    assert marker.read_text(encoding="utf-8") == "keep"

    marker.unlink()
    watch.rmdir()
    outside = tmp_path / "outside-watch"
    outside.mkdir()
    watch.symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.cleanup_retired_directories("admin")
    assert watch.is_symlink()
