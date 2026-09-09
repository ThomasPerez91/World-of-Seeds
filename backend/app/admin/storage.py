from dataclasses import dataclass
from pathlib import Path

from app.storage import SharedContentStore, SharedContentStoreError


class AdminStorageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AdminFilesystemUsage:
    total: int
    used: int
    available: int


class AdminStorageInspector:
    """Read shared-storage filesystem usage without traversing content."""

    def __init__(self, data_root: Path) -> None:
        self._store = SharedContentStore(data_root)

    def inspect(self) -> AdminFilesystemUsage:
        try:
            total, available = self._store.disk_capacity()
        except (OSError, SharedContentStoreError) as exc:
            raise AdminStorageError("Filesystem usage is unavailable") from exc
        return AdminFilesystemUsage(
            total=total,
            used=max(total - available, 0),
            available=available,
        )
