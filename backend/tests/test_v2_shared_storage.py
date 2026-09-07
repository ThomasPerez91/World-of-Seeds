import os
import uuid
from pathlib import Path

import pytest

from app.storage import SharedContentStore, SharedContentStoreError

STORAGE_KEY = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _store(tmp_path: Path) -> SharedContentStore:
    root = tmp_path / "data"
    root.mkdir()
    return SharedContentStore(root)


def test_store_prepares_one_opaque_directory_idempotently(tmp_path: Path) -> None:
    store = _store(tmp_path)

    store.prepare(STORAGE_KEY)
    store.prepare(STORAGE_KEY)

    expected = tmp_path / "data" / "content" / STORAGE_KEY.hex
    assert expected.is_dir()
    assert [entry.name for entry in expected.parent.iterdir()] == [STORAGE_KEY.hex]
    with store.open_directory(STORAGE_KEY) as descriptor:
        assert os.path.samestat(os.fstat(descriptor), expected.stat())
    with pytest.raises(OSError):
        os.fstat(descriptor)

    total_bytes, free_bytes = store.disk_capacity()
    assert total_bytes > 0
    assert 0 <= free_bytes <= total_bytes


def test_store_rejects_symlinked_content_root(tmp_path: Path) -> None:
    root = tmp_path / "data"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "content").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SharedContentStoreError):
        SharedContentStore(root).prepare(STORAGE_KEY)

    assert list(outside.iterdir()) == []


def test_store_rejects_symlink_collision_for_opaque_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    content = tmp_path / "data" / "content"
    outside = tmp_path / "outside"
    content.mkdir()
    outside.mkdir()
    (content / STORAGE_KEY.hex).symlink_to(outside, target_is_directory=True)

    with pytest.raises(SharedContentStoreError):
        store.prepare(STORAGE_KEY)
    with pytest.raises(SharedContentStoreError), store.open_directory(STORAGE_KEY):
        pass

    assert list(outside.iterdir()) == []


def test_store_removes_only_empty_managed_directory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.prepare(STORAGE_KEY)
    managed = tmp_path / "data" / "content" / STORAGE_KEY.hex
    (managed / "payload.bin").write_bytes(b"content")

    with pytest.raises(SharedContentStoreError):
        store.remove_empty(STORAGE_KEY)
    assert (managed / "payload.bin").read_bytes() == b"content"

    (managed / "payload.bin").unlink()
    store.remove_empty(STORAGE_KEY)
    assert not managed.exists()


def test_store_purges_nested_content_without_following_symlinks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.prepare(STORAGE_KEY)
    managed = tmp_path / "data" / "content" / STORAGE_KEY.hex
    nested = managed / "season"
    nested.mkdir()
    (nested / "episode.mkv").write_bytes(b"content")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (managed / "outside-link").symlink_to(outside, target_is_directory=True)

    assert store.purge(STORAGE_KEY) == 3
    assert not managed.exists()
    assert (outside / "keep.txt").read_text() == "keep"
    assert store.purge(STORAGE_KEY) == 0


def test_store_rejects_non_uuid_and_zero_uuid(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.prepare(uuid.UUID(int=0))
    with pytest.raises(ValueError):
        store.prepare("not-a-uuid")  # type: ignore[arg-type]
