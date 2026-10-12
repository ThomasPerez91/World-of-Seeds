from __future__ import annotations

import logging
import os
import stat
import threading
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.torrents import ParsedTorrent, sanitize_torrent

MAX_STAGED_TORRENT_BYTES = 32 * 1024 * 1024
MAX_MANAGED_TORRENT_BYTES = 10_995_116_277_760


# The API runs in one process. Serialize publish/conditional cleanup for a key,
# with bounded lock storage; slow preparation uses a private attempt UUID.
logger = logging.getLogger(__name__)
_PUBLICATION_LOCKS = tuple(threading.Lock() for _ in range(256))


@dataclass(slots=True)
class OwnedTorrentPayload:
    parsed: ParsedTorrent
    storage_key: UUID
    descriptor: int

    def close(self) -> None:
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


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

    def stage_owned(
        self,
        content: bytes,
        *,
        storage_key: UUID,
        max_total_size: int | None = None,
    ) -> OwnedTorrentPayload:
        attempt_key = uuid.uuid4()
        directory_fd: int | None = None
        owned: OwnedTorrentPayload | None = None
        published = False
        try:
            parsed = self.stage(content, storage_key=attempt_key, max_total_size=max_total_size)
            directory_fd = self._open_root(create=False)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self._name(attempt_key), flags, dir_fd=directory_fd)
            owned = OwnedTorrentPayload(parsed, storage_key, descriptor)
            with self._publication_lock(storage_key):
                os.replace(
                    self._name(attempt_key),
                    self._name(storage_key),
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
                published = True
                os.fsync(directory_fd)
            return owned
        except BaseException as exc:
            if published and owned is not None:
                try:
                    self.remove_owned(owned)
                except TorrentPayloadStoreError:
                    logger.warning("torrent_payload_cleanup_failed")
            if isinstance(exc, OSError):
                raise TorrentPayloadStoreError("torrent payload could not be published") from exc
            raise
        finally:
            if owned is not None and not published:
                owned.close()
            if directory_fd is not None:
                os.close(directory_fd)
            # Also removes an attempt whose stage failed after its atomic rename.
            try:
                self.remove(attempt_key)
            except TorrentPayloadStoreError:
                logger.warning("torrent_payload_cleanup_failed")

    def remove_owned(self, owned: OwnedTorrentPayload) -> None:
        """Remove only this attempt's inode; an open FD prevents inode reuse."""
        directory_fd: int | None = None
        try:
            directory_fd = self._open_root(create=False)
            with self._publication_lock(owned.storage_key):
                original = os.fstat(owned.descriptor)
                try:
                    current = os.stat(
                        self._name(owned.storage_key),
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    return
                if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                    return
                os.unlink(self._name(owned.storage_key), dir_fd=directory_fd)
                os.fsync(directory_fd)
        except OSError as exc:
            raise TorrentPayloadStoreError("torrent payload could not be cleaned") from exc
        finally:
            if directory_fd is not None:
                os.close(directory_fd)
            owned.close()

    @staticmethod
    def _publication_lock(storage_key: UUID) -> threading.Lock:
        return _PUBLICATION_LOCKS[storage_key.int % len(_PUBLICATION_LOCKS)]

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
