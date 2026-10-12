import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.storage.shared import SharedContentStore


def test_inventory_uses_entry_types_and_closes_the_directory_iterator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = tmp_path / "content"
    content.mkdir()
    for number in (2, 1):
        (content / uuid.UUID(int=number).hex).mkdir()
    (content / uuid.UUID(int=3).hex).symlink_to(tmp_path, target_is_directory=True)
    (content / uuid.UUID(int=4).hex).write_text("not a directory")
    (content / "invalid-name").mkdir()
    original = os.scandir
    stats = 0
    directory_types = 0
    closed = False

    class Entry:
        def __init__(self, real: Any) -> None:
            self.real = real
            self.name = real.name

        def stat(self, *, follow_symlinks: bool) -> Any:
            nonlocal stats
            stats += 1
            return self.real.stat(follow_symlinks=follow_symlinks)

        def is_dir(self, *, follow_symlinks: bool) -> bool:
            nonlocal directory_types
            directory_types += 1
            assert not follow_symlinks
            return bool(self.real.is_dir(follow_symlinks=follow_symlinks))

    class Scan:
        def __init__(self, path: Any) -> None:
            self.real = original(path)

        def __iter__(self) -> Any:
            return (Entry(entry) for entry in self.real)

        def __enter__(self) -> "Scan":
            return self

        def __exit__(self, *args: object) -> None:
            nonlocal closed
            self.real.close()
            closed = True

    monkeypatch.setattr(os, "scandir", Scan)
    result = SharedContentStore(tmp_path).inventory_page(limit=1)
    assert result.keys == (uuid.UUID(int=1),)
    assert result.invalid_entries == 3
    assert result.truncated
    assert stats == 0
    assert directory_types == 4
    assert closed
