from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID


class SharedContentStoreError(RuntimeError):
    """A managed content directory is missing, unsafe, or cannot be prepared."""


class SharedContentStore:
    """Descriptor-based access to opaque V2 physical content directories.

    Callers provide only the server-owned storage UUID. No user path is accepted and no
    absolute host path is returned. Every directory hop is opened with ``O_NOFOLLOW`` when
    supported so a symlink collision fails closed before qBittorrent is called.
    """

    def __init__(self, data_root: Path) -> None:
        if not data_root.is_absolute():
            raise ValueError("shared content data root must be absolute")
        self._data_root = data_root

    def prepare(self, storage_key: UUID) -> None:
        """Create the single opaque directory idempotently and validate its descriptor."""
        name = self._name(storage_key)
        content_fd = self._open_content_root(create=True)
        managed_fd: int | None = None
        try:
            try:
                os.mkdir(name, mode=0o750, dir_fd=content_fd)
                os.fsync(content_fd)
            except FileExistsError:
                pass
            managed_fd = self._open_directory(name, dir_fd=content_fd)
        except OSError as exc:
            raise SharedContentStoreError("managed content directory is unsafe") from exc
        finally:
            if managed_fd is not None:
                os.close(managed_fd)
            os.close(content_fd)

    def disk_capacity(self) -> tuple[int, int]:
        """Return total and available bytes from the validated data-root descriptor."""
        descriptor: int | None = None
        try:
            descriptor = self._open_directory(self._data_root)
            values = os.fstatvfs(descriptor)
            fragment_size = values.f_frsize or values.f_bsize
            return values.f_blocks * fragment_size, values.f_bavail * fragment_size
        except OSError as exc:
            raise SharedContentStoreError("shared storage capacity is unavailable") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @contextmanager
    def open_directory(self, storage_key: UUID) -> Iterator[int]:
        """Yield a validated directory descriptor without exposing a filesystem path."""
        name = self._name(storage_key)
        content_fd = self._open_content_root(create=False)
        managed_fd: int | None = None
        try:
            managed_fd = self._open_directory(name, dir_fd=content_fd)
            yield managed_fd
        except FileNotFoundError as exc:
            raise SharedContentStoreError("managed content directory is missing") from exc
        except OSError as exc:
            raise SharedContentStoreError("managed content directory is unsafe") from exc
        finally:
            if managed_fd is not None:
                os.close(managed_fd)
            os.close(content_fd)

    def remove_empty(self, storage_key: UUID) -> None:
        """Remove only an empty managed directory; never recurse or follow links."""
        name = self._name(storage_key)
        content_fd = self._open_content_root(create=False)
        managed_fd: int | None = None
        try:
            managed_fd = self._open_directory(name, dir_fd=content_fd)
            os.close(managed_fd)
            managed_fd = None
            os.rmdir(name, dir_fd=content_fd)
            os.fsync(content_fd)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SharedContentStoreError(
                "managed content directory is not empty or unsafe"
            ) from exc
        finally:
            if managed_fd is not None:
                os.close(managed_fd)
            os.close(content_fd)

    def _open_content_root(self, *, create: bool) -> int:
        data_fd: int | None = None
        try:
            data_fd = self._open_directory(self._data_root)
            if create:
                try:
                    os.mkdir("content", mode=0o750, dir_fd=data_fd)
                    os.fsync(data_fd)
                except FileExistsError:
                    pass
            return self._open_directory("content", dir_fd=data_fd)
        except FileNotFoundError as exc:
            raise SharedContentStoreError("shared content root is missing") from exc
        except OSError as exc:
            raise SharedContentStoreError("shared content root is unsafe") from exc
        finally:
            if data_fd is not None:
                os.close(data_fd)

    @staticmethod
    def _open_directory(path: str | Path, *, dir_fd: int | None = None) -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(path, flags, dir_fd=dir_fd)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(descriptor)
            raise SharedContentStoreError("shared content entry is not a directory")
        return descriptor

    @staticmethod
    def _name(storage_key: UUID) -> str:
        if not isinstance(storage_key, UUID) or storage_key.int == 0:
            raise ValueError("storage key must be a non-zero UUID")
        return storage_key.hex
