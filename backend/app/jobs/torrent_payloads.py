from __future__ import annotations

import os
import stat
import uuid
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from app.torrents import ParsedTorrent, sanitize_torrent

MAX_STAGED_TORRENT_BYTES = 32 * 1024 * 1024
MAX_MANAGED_TORRENT_BYTES = 10_995_116_277_760


class TorrentPayloadStoreError(RuntimeError):
    """A secret-free staged torrent payload is missing or unsafe."""


class TorrentPayloadStore:
    """Private spool containing only tracker-sanitized metainfo.

    The path is derived exclusively from the server-owned storage UUID. Uploaded tracker
    credentials are removed before the first durable write; the infrastructure passkey is
    injected later by the worker and never reaches this store.
    """

    def __init__(
        self,
        data_root: Path,
        *,
        allowed_tracker_hosts: list[str],
        max_total_size: int = MAX_MANAGED_TORRENT_BYTES,
    ) -> None:
        if not data_root.is_absolute():
            raise ValueError("torrent payload data root must be absolute")
        if not allowed_tracker_hosts or max_total_size <= 0:
            raise ValueError("torrent payload validation configuration is invalid")
        self._root = data_root / "control" / "torrent-input"
        self._allowed_tracker_hosts = list(allowed_tracker_hosts)
        self._max_total_size = max_total_size

    def stage(
        self,
        content: bytes,
        *,
        storage_key: UUID,
        max_total_size: int | None = None,
    ) -> ParsedTorrent:
        if len(content) > MAX_STAGED_TORRENT_BYTES:
            raise TorrentPayloadStoreError("torrent payload exceeds the staging limit")
        parsed = sanitize_torrent(
            content,
            allowed_tracker_hosts=self._allowed_tracker_hosts,
            max_total_size=self._max_total_size if max_total_size is None else max_total_size,
        )
        if len(parsed.content) > MAX_STAGED_TORRENT_BYTES:
            raise TorrentPayloadStoreError("sanitized torrent exceeds the staging limit")

        directory_fd = self._open_root()
        temporary_name = f".{storage_key.hex}.{uuid.uuid4().hex}.tmp"
        final_name = self._name(storage_key)
        file_fd: int | None = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            file_fd = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
            view = memoryview(parsed.content)
            while view:
                written = os.write(file_fd, view)
                if written <= 0:
                    raise TorrentPayloadStoreError("torrent payload could not be staged")
                view = view[written:]
            os.fsync(file_fd)
            os.close(file_fd)
            file_fd = None
            os.replace(
                temporary_name,
                final_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        except OSError as exc:
            raise TorrentPayloadStoreError("torrent payload could not be staged") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=directory_fd)
            os.close(directory_fd)
        return parsed

    def read(self, storage_key: UUID) -> ParsedTorrent:
        directory_fd = self._open_root(create=False)
        file_fd: int | None = None
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            file_fd = os.open(self._name(storage_key), flags, dir_fd=directory_fd)
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or not (
                0 < metadata.st_size <= MAX_STAGED_TORRENT_BYTES
            ):
                raise TorrentPayloadStoreError("staged torrent payload is unsafe")
            chunks: list[bytes] = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(file_fd, min(remaining, 64 * 1024))
                if not chunk:
                    raise TorrentPayloadStoreError("staged torrent payload is incomplete")
                chunks.append(chunk)
                remaining -= len(chunk)
        except FileNotFoundError as exc:
            raise TorrentPayloadStoreError("staged torrent payload is missing") from exc
        except OSError as exc:
            raise TorrentPayloadStoreError("staged torrent payload could not be read") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            os.close(directory_fd)

        try:
            return sanitize_torrent(
                b"".join(chunks),
                allowed_tracker_hosts=self._allowed_tracker_hosts,
                max_total_size=self._max_total_size,
            )
        except ValueError as exc:
            raise TorrentPayloadStoreError("staged torrent payload is invalid") from exc

    def remove(self, storage_key: UUID) -> None:
        try:
            directory_fd = self._open_root(create=False)
        except TorrentPayloadStoreError:
            return
        try:
            os.unlink(self._name(storage_key), dir_fd=directory_fd)
            os.fsync(directory_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise TorrentPayloadStoreError("staged torrent payload could not be removed") from exc
        finally:
            os.close(directory_fd)

    def _open_root(self, *, create: bool = True) -> int:
        try:
            if create:
                self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            directory_fd = os.open(self._root, flags)
            metadata = os.fstat(directory_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                os.close(directory_fd)
                raise TorrentPayloadStoreError("torrent payload root is unsafe")
            return directory_fd
        except OSError as exc:
            raise TorrentPayloadStoreError("torrent payload root is unavailable") from exc

    @staticmethod
    def _name(storage_key: UUID) -> str:
        if not isinstance(storage_key, UUID):
            raise ValueError("storage key must be a UUID")
        return f"{storage_key.hex}.torrent"
