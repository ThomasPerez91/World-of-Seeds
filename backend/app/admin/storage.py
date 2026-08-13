import os
from dataclasses import dataclass
from pathlib import Path

from app.files.workspaces import DIRECTORY_OPEN_FLAGS


class AdminStorageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AdminFilesystemUsage:
    total: int
    used: int
    available: int


class AdminStorageInspector:
    """Read filesystem-level usage without traversing user data."""

    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root

    def inspect(self) -> AdminFilesystemUsage:
        try:
            data_fd = os.open(self._data_root, DIRECTORY_OPEN_FLAGS)
        except OSError as exc:
            raise AdminStorageError("The data root is unavailable") from exc
        try:
            filesystem = os.fstatvfs(data_fd)
        except OSError as exc:
            raise AdminStorageError("Filesystem usage is unavailable") from exc
        finally:
            os.close(data_fd)

        block_size = filesystem.f_frsize or filesystem.f_bsize
        total = filesystem.f_blocks * block_size
        free = filesystem.f_bfree * block_size
        available = filesystem.f_bavail * block_size
        return AdminFilesystemUsage(
            total=total,
            used=max(total - free, 0),
            available=available,
        )
