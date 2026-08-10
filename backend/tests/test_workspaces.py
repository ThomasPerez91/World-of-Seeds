from pathlib import Path

import pytest

from app.files import (
    WorkspaceAlreadyExistsError,
    WorkspaceCompensationError,
    WorkspaceManager,
    WorkspaceUnsafeEntryError,
)


def make_manager(tmp_path: Path) -> tuple[WorkspaceManager, Path]:
    data_root = tmp_path / "data"
    data_root.mkdir()
    return WorkspaceManager(data_root), data_root


def test_workspace_is_created_and_renamed_without_touching_legacy_paths(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    (data_root / "downloads").mkdir()
    (data_root / "watch").mkdir()
    legacy_marker = data_root / "downloads" / "still-seeding.mkv"
    legacy_marker.write_bytes(b"legacy")

    manager.create("guest-123abc")
    manager.rename("guest-123abc", "thomas")

    workspace = data_root / "users" / "thomas"
    assert {entry.name for entry in workspace.iterdir()} == {"downloads", "watch"}
    assert legacy_marker.read_bytes() == b"legacy"
    assert not (data_root / "users" / "guest-123abc").exists()


@pytest.mark.parametrize(
    "username",
    ["../../etc/passwd", "/tmp/outside", "alice/bob", "..", ".", "Alice", "bad\0name"],
)
def test_workspace_names_are_single_normalized_components(tmp_path: Path, username: str) -> None:
    manager, data_root = make_manager(tmp_path)

    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.create(username)

    assert list(data_root.iterdir()) == []


def test_users_symlink_is_never_followed(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (data_root / "users").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.create("thomas")

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


def test_workspace_symlink_is_never_adopted_or_renamed(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    users = data_root / "users"
    users.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (users / "thomas").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceAlreadyExistsError):
        manager.create("thomas")
    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.assert_ready("thomas")
    with pytest.raises(WorkspaceUnsafeEntryError):
        manager.rename("thomas", "renamed")

    assert (users / "thomas").is_symlink()
    assert list(outside.iterdir()) == []


def test_workspace_child_symlink_is_never_followed(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    manager.create("thomas")
    downloads = data_root / "users" / "thomas" / "downloads"
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

    assert not (data_root / "users" / "thomas").exists()


def test_rename_is_compensated_when_the_database_operation_fails(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    manager.create("temporary")
    marker = data_root / "users" / "temporary" / "downloads" / "video.mkv"
    marker.write_bytes(b"content")

    with (
        pytest.raises(RuntimeError, match="database failed"),
        manager.rename_for_transaction("temporary", "thomas"),
    ):
        raise RuntimeError("database failed")

    assert marker.read_bytes() == b"content"
    assert not (data_root / "users" / "thomas").exists()


def test_compensation_never_deletes_a_non_empty_workspace(tmp_path: Path) -> None:
    manager, data_root = make_manager(tmp_path)
    manager.create("thomas")
    marker = data_root / "users" / "thomas" / "downloads" / "video.mkv"
    marker.write_bytes(b"content")

    with pytest.raises(WorkspaceCompensationError):
        manager.remove_empty("thomas")

    assert marker.read_bytes() == b"content"
