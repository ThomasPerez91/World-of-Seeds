"""Recoverable per-user trash lifecycle."""

from app.trash.filesystem import (
    TrashFilesystem,
    TrashFilesystemEntry,
    TrashPurgeError,
    TrashStorageError,
    TrashStorageMissingError,
    TrashStorageUnsafeError,
)
from app.trash.service import (
    TrashCompensationError,
    TrashEntryNotFoundError,
    TrashListing,
    TrashPersistenceError,
    TrashRestoreTargetMissingError,
    TrashService,
)

__all__ = [
    "TrashCompensationError",
    "TrashEntryNotFoundError",
    "TrashFilesystem",
    "TrashFilesystemEntry",
    "TrashListing",
    "TrashPersistenceError",
    "TrashPurgeError",
    "TrashRestoreTargetMissingError",
    "TrashService",
    "TrashStorageError",
    "TrashStorageMissingError",
    "TrashStorageUnsafeError",
]
