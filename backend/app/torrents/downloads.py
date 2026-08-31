from __future__ import annotations

import asyncio
import errno
import hashlib
import mimetypes
import os
import stat
import time
import uuid
import zipfile
from collections.abc import AsyncGenerator, Callable, Generator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from threading import BoundedSemaphore
from typing import IO, cast
from urllib.parse import quote

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.files.downloads import OpenedDownload
from app.models import (
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.storage import SharedContentStore

_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
_managed_archive_slot = BoundedSemaphore(value=1)


class ManagedDownloadError(RuntimeError):
    """A managed file no longer matches its authoritative manifest entry."""


class DownloadConcurrencyError(RuntimeError):
    """The durable per-user concurrent download limit was reached."""


class ManagedArchiveBusyError(RuntimeError):
    """The bounded managed ZIP slot is already in use."""


@dataclass(frozen=True, slots=True)
class ManagedArchiveEntry:
    relative_path: str
    size: int
    file_index: int


class _StreamingZipBuffer:
    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._position = 0

    def write(self, data: bytes | bytearray | memoryview) -> int:
        chunk = bytes(data)
        if chunk:
            self._chunks.append(chunk)
            self._position += len(chunk)
        return len(chunk)

    def tell(self) -> int:
        return self._position

    @staticmethod
    def seekable() -> bool:
        return False

    @staticmethod
    def seek(*_: object) -> int:
        raise OSError("streaming ZIP output is not seekable")

    @staticmethod
    def flush() -> None:
        return None

    def drain(self) -> tuple[bytes, ...]:
        chunks, self._chunks = tuple(self._chunks), []
        return chunks


def download_snapshot_id(
    torrent_request_id: uuid.UUID,
    manifest_checksum: str,
    manifest_version: int,
) -> str:
    source = (
        b"world-of-seeds-v2-download-snapshot\x00"
        + torrent_request_id.bytes
        + manifest_version.to_bytes(8, "big")
        + manifest_checksum.encode("ascii")
    )
    return hashlib.sha256(source).hexdigest()


class ManagedFileDownloader:
    def __init__(self, store: SharedContentStore) -> None:
        self._store = store

    def open(
        self,
        storage_key: uuid.UUID,
        relative_path: str,
        *,
        expected_size: int,
        manifest_checksum: str,
        manifest_version: int,
        file_index: int,
    ) -> OpenedDownload:
        components = self._components(relative_path)
        with self._store.open_directory(storage_key) as root_fd:
            parent_fd = os.dup(root_fd)
            try:
                for component in components[:-1]:
                    next_fd = self._open_directory(parent_fd, component)
                    os.close(parent_fd)
                    parent_fd = next_fd
                file_fd = self._open_file(parent_fd, components[-1])
            finally:
                os.close(parent_fd)
        try:
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != expected_size:
                raise ManagedDownloadError("managed file does not match its manifest")
            modified_at = datetime.fromtimestamp(metadata.st_mtime, tz=UTC).replace(microsecond=0)
            etag_source = f"{manifest_checksum}:{manifest_version}:{file_index}:{expected_size}"
            etag = f'"{hashlib.sha256(etag_source.encode()).hexdigest()}"'
            name = components[-1]
            return OpenedDownload(
                file_descriptor=file_fd,
                name=name,
                size=expected_size,
                modified_at=modified_at,
                media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                etag=etag,
            )
        except BaseException:
            os.close(file_fd)
            raise

    @staticmethod
    def _components(relative_path: str) -> tuple[str, ...]:
        parsed = PurePosixPath(relative_path)
        if (
            not relative_path
            or len(relative_path) > 4096
            or relative_path.startswith("/")
            or "\\" in relative_path
            or "\x00" in relative_path
            or parsed.is_absolute()
            or parsed.as_posix() != relative_path
            or any(component in {"", ".", ".."} for component in parsed.parts)
        ):
            raise ManagedDownloadError("managed file path is invalid")
        return parsed.parts

    @staticmethod
    def _open_directory(parent_fd: int, name: str) -> int:
        try:
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise ManagedDownloadError("managed path component is not a directory")
            return descriptor
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ManagedDownloadError("managed file is missing") from exc
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ManagedDownloadError("managed path is unsafe") from exc
            raise ManagedDownloadError("managed directory cannot be opened") from exc

    @staticmethod
    def _open_file(parent_fd: int, name: str) -> int:
        try:
            return os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ManagedDownloadError("managed file is missing") from exc
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise ManagedDownloadError("managed file is unsafe") from exc
            raise ManagedDownloadError("managed file cannot be opened") from exc


class ManagedFolderArchiver:
    def __init__(
        self,
        downloader: ManagedFileDownloader,
        *,
        storage_key: uuid.UUID,
        entries: tuple[ManagedArchiveEntry, ...],
        manifest_checksum: str,
        manifest_version: int,
        download_name: str,
    ) -> None:
        self._downloader = downloader
        self._storage_key = storage_key
        self._entries = entries
        self._manifest_checksum = manifest_checksum
        self._manifest_version = manifest_version
        self._download_name = download_name
        self._acquired = False

    def acquire(self) -> None:
        if not _managed_archive_slot.acquire(blocking=False):
            raise ManagedArchiveBusyError("another managed archive is already running")
        self._acquired = True

    def release(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        _managed_archive_slot.release()

    @property
    def content_disposition(self) -> str:
        fallback = (
            "".join(
                character
                for character in self._download_name
                if character.isascii() and character.isprintable() and character not in {'"', "\\"}
            ).strip()
            or "download.zip"
        )
        return (
            f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(self._download_name, safe='')}"
        )

    def iter_chunks(self, *, chunk_size: int) -> Generator[bytes, None, None]:
        return self._iter_chunks(chunk_size=chunk_size)

    def _iter_chunks(self, *, chunk_size: int) -> Generator[bytes, None, None]:
        writer = _StreamingZipBuffer()
        try:
            with zipfile.ZipFile(
                cast("IO[bytes]", writer),
                mode="w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                for entry in self._entries:
                    opened = self._downloader.open(
                        self._storage_key,
                        entry.relative_path,
                        expected_size=entry.size,
                        manifest_checksum=self._manifest_checksum,
                        manifest_version=self._manifest_version,
                        file_index=entry.file_index,
                    )
                    try:
                        info = zipfile.ZipInfo(
                            entry.relative_path,
                            opened.modified_at.timetuple()[:6],
                        )
                        info.compress_type = zipfile.ZIP_STORED
                        info.external_attr = 0o100640 << 16
                        remaining = entry.size
                        with archive.open(info, mode="w", force_zip64=True) as target:
                            while remaining > 0:
                                chunk = os.read(opened.file_descriptor, min(chunk_size, remaining))
                                if not chunk:
                                    raise ManagedDownloadError(
                                        "managed archive file ended before manifest size"
                                    )
                                target.write(chunk)
                                remaining -= len(chunk)
                                yield from writer.drain()
                        yield from writer.drain()
                    finally:
                        opened.close()
            yield from writer.drain()
        finally:
            self.release()


class DownloadLeaseManager:
    def __init__(
        self,
        session: AsyncSession,
        *,
        lease_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("download lease duration is invalid")
        self._session = session
        self._lease_seconds = lease_seconds
        self._clock = clock

    @property
    def heartbeat_seconds(self) -> float:
        return max(1.0, self._lease_seconds / 3)

    async def acquire(
        self,
        *,
        user_id: uuid.UUID,
        managed_torrent_id: uuid.UUID,
        torrent_request_id: uuid.UUID,
        torrent_file_id: uuid.UUID,
        max_concurrent: int,
    ) -> DownloadLease:
        if not 1 <= max_concurrent <= 20:
            raise ValueError("download concurrency limit is invalid")
        now = self._clock()
        async with self._session.begin():
            locked_user_id = await self._session.scalar(
                select(User.id).where(User.id == user_id).with_for_update()
            )
            if locked_user_id is None:
                raise ManagedDownloadError("download owner is missing")
            locked_right_id = await self._session.scalar(
                select(TorrentRequest.id)
                .join(ManagedTorrent, ManagedTorrent.id == TorrentRequest.managed_torrent_id)
                .join(TorrentFile, TorrentFile.managed_torrent_id == ManagedTorrent.id)
                .where(
                    TorrentRequest.id == torrent_request_id,
                    TorrentRequest.user_id == user_id,
                    TorrentRequest.managed_torrent_id == managed_torrent_id,
                    TorrentRequest.state == TorrentRequestState.READY,
                    ManagedTorrent.id == managed_torrent_id,
                    ManagedTorrent.state == ManagedTorrentState.READY,
                    ManagedTorrent.retention_expires_at.is_not(None),
                    ManagedTorrent.retention_expires_at > now,
                    TorrentFile.id == torrent_file_id,
                )
                .with_for_update()
            )
            if locked_right_id is None:
                raise ManagedDownloadError("download right is no longer ready")
            await self._session.execute(
                delete(DownloadLease).where(
                    DownloadLease.user_id == user_id,
                    DownloadLease.expires_at <= now,
                )
            )
            active = await self._session.scalar(
                select(func.count())
                .select_from(DownloadLease)
                .where(
                    DownloadLease.user_id == user_id,
                    DownloadLease.expires_at > now,
                )
            )
            if active is None or active >= max_concurrent:
                raise DownloadConcurrencyError("download concurrency limit reached")
            lease = DownloadLease(
                user_id=user_id,
                managed_torrent_id=managed_torrent_id,
                torrent_request_id=torrent_request_id,
                torrent_file_id=torrent_file_id,
                expires_at=now + timedelta(seconds=self._lease_seconds),
                created_at=now,
                renewed_at=now,
            )
            self._session.add(lease)
            await self._session.flush()
        return lease

    async def renew(self, lease_id: uuid.UUID) -> None:
        now = self._clock()
        async with self._session.begin():
            candidate = await self._session.get(DownloadLease, lease_id)
            if candidate is None:
                raise ManagedDownloadError("download lease was lost")
            user = await self._session.get(User, candidate.user_id, with_for_update=True)
            managed = await self._session.get(
                ManagedTorrent,
                candidate.managed_torrent_id,
                with_for_update=True,
            )
            lease = await self._session.get(DownloadLease, lease_id, with_for_update=True)
            request = (
                await self._session.get(
                    TorrentRequest,
                    candidate.torrent_request_id,
                    with_for_update=True,
                )
                if lease is not None
                else None
            )
            ready_right = (
                managed is not None
                and managed.state is ManagedTorrentState.READY
                and managed.retention_expires_at is not None
                and _as_utc(managed.retention_expires_at) > _as_utc(now)
                and request is not None
                and request.state is TorrentRequestState.READY
            )
            finishing_expired_download = (
                managed is not None
                and managed.state is ManagedTorrentState.PURGE_PENDING
                and request is not None
                and request.state is TorrentRequestState.EXPIRED
            )
            if user is None or lease is None or not (ready_right or finishing_expired_download):
                raise ManagedDownloadError("download lease was lost")
            lease.renewed_at = now
            lease.expires_at = now + timedelta(seconds=self._lease_seconds)

    async def release(self, lease_id: uuid.UUID) -> None:
        await self._session.rollback()
        async with self._session.begin():
            await self._session.execute(delete(DownloadLease).where(DownloadLease.id == lease_id))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class DownloadRateLimiter:
    """Bound byte reservations for all streams served by one API process."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._global_next = 0.0
        self._user_next: dict[uuid.UUID, float] = {}

    async def reserve(
        self,
        user_id: uuid.UUID,
        byte_count: int,
        *,
        per_user_bytes_per_second: int,
        global_bytes_per_second: int,
    ) -> float:
        if byte_count < 0 or per_user_bytes_per_second < 0 or global_bytes_per_second < 0:
            raise ValueError("download rate values must be non-negative")
        async with self._lock:
            now = self._clock()
            user_ready = self._user_next.get(user_id, now)
            global_ready = self._global_next
            ready_at = max(
                now,
                user_ready if per_user_bytes_per_second else now,
                global_ready if global_bytes_per_second else now,
            )
            if per_user_bytes_per_second:
                self._user_next[user_id] = ready_at + byte_count / per_user_bytes_per_second
            if global_bytes_per_second:
                self._global_next = ready_at + byte_count / global_bytes_per_second
            if len(self._user_next) > 10_000:
                self._user_next = {
                    key: value for key, value in self._user_next.items() if value > now
                }
            return max(0.0, ready_at - now)


async def stream_managed_download(
    download: OpenedDownload,
    *,
    start: int,
    length: int,
    chunk_size: int,
    user_id: uuid.UUID,
    lease: DownloadLease,
    leases: DownloadLeaseManager,
    limiter: DownloadRateLimiter,
    per_user_bytes_per_second: int,
    global_bytes_per_second: int,
) -> AsyncGenerator[bytes, None]:
    effective_rates = [
        rate for rate in (per_user_bytes_per_second, global_bytes_per_second) if rate > 0
    ]
    if effective_rates:
        chunk_size = min(
            chunk_size,
            max(1, int(min(effective_rates) * leases.heartbeat_seconds)),
        )
    offset = start
    remaining = length
    next_renewal = time.monotonic() + leases.heartbeat_seconds
    try:
        while remaining > 0:
            if time.monotonic() >= next_renewal:
                await leases.renew(lease.id)
                next_renewal = time.monotonic() + leases.heartbeat_seconds
            requested = min(remaining, chunk_size)
            delay = await limiter.reserve(
                user_id,
                requested,
                per_user_bytes_per_second=per_user_bytes_per_second,
                global_bytes_per_second=global_bytes_per_second,
            )
            while delay > 0:
                interval = min(delay, leases.heartbeat_seconds)
                await asyncio.sleep(interval)
                delay -= interval
                await leases.renew(lease.id)
                next_renewal = time.monotonic() + leases.heartbeat_seconds
            chunk = await run_in_threadpool(
                os.pread,
                download.file_descriptor,
                requested,
                offset,
            )
            if not chunk:
                raise ManagedDownloadError("managed file ended before its manifest size")
            yield chunk
            offset += len(chunk)
            remaining -= len(chunk)
    finally:
        download.close()


def _next_archive_chunk(iterator: Iterator[bytes]) -> bytes | None:
    return next(iterator, None)


async def stream_managed_archive(
    archiver: ManagedFolderArchiver,
    *,
    chunk_size: int,
    user_id: uuid.UUID,
    lease: DownloadLease,
    leases: DownloadLeaseManager,
    limiter: DownloadRateLimiter,
    per_user_bytes_per_second: int,
    global_bytes_per_second: int,
) -> AsyncGenerator[bytes, None]:
    chunks = archiver.iter_chunks(chunk_size=chunk_size)
    next_renewal = time.monotonic() + leases.heartbeat_seconds
    try:
        while True:
            chunk = await run_in_threadpool(_next_archive_chunk, chunks)
            if chunk is None:
                return
            if time.monotonic() >= next_renewal:
                await leases.renew(lease.id)
                next_renewal = time.monotonic() + leases.heartbeat_seconds
            delay = await limiter.reserve(
                user_id,
                len(chunk),
                per_user_bytes_per_second=per_user_bytes_per_second,
                global_bytes_per_second=global_bytes_per_second,
            )
            while delay > 0:
                interval = min(delay, leases.heartbeat_seconds)
                await asyncio.sleep(interval)
                delay -= interval
                await leases.renew(lease.id)
                next_renewal = time.monotonic() + leases.heartbeat_seconds
            yield chunk
    finally:
        await run_in_threadpool(chunks.close)
        archiver.release()


class ManagedDownloadStreamingResponse(StreamingResponse):
    def __init__(
        self,
        download: OpenedDownload,
        *,
        start: int,
        length: int,
        status_code: int,
        headers: dict[str, str],
        chunk_size: int,
        user_id: uuid.UUID,
        lease: DownloadLease,
        leases: DownloadLeaseManager,
        limiter: DownloadRateLimiter,
        per_user_bytes_per_second: int,
        global_bytes_per_second: int,
    ) -> None:
        self._download = download
        self._lease = lease
        self._leases = leases
        super().__init__(
            stream_managed_download(
                download,
                start=start,
                length=length,
                chunk_size=chunk_size,
                user_id=user_id,
                lease=lease,
                leases=leases,
                limiter=limiter,
                per_user_bytes_per_second=per_user_bytes_per_second,
                global_bytes_per_second=global_bytes_per_second,
            ),
            status_code=status_code,
            headers=headers,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._download.close()
            await self._leases.release(self._lease.id)


class ManagedArchiveStreamingResponse(StreamingResponse):
    def __init__(
        self,
        archiver: ManagedFolderArchiver,
        *,
        chunk_size: int,
        user_id: uuid.UUID,
        lease: DownloadLease,
        leases: DownloadLeaseManager,
        limiter: DownloadRateLimiter,
        per_user_bytes_per_second: int,
        global_bytes_per_second: int,
    ) -> None:
        self._archiver = archiver
        self._lease = lease
        self._leases = leases
        super().__init__(
            stream_managed_archive(
                archiver,
                chunk_size=chunk_size,
                user_id=user_id,
                lease=lease,
                leases=leases,
                limiter=limiter,
                per_user_bytes_per_second=per_user_bytes_per_second,
                global_bytes_per_second=global_bytes_per_second,
            ),
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": archiver.content_disposition,
            },
            media_type="application/zip",
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._archiver.release()
            await self._leases.release(self._lease.id)
