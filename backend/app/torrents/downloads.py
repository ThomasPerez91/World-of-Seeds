from __future__ import annotations

import asyncio
import errno
import hashlib
import mimetypes
import os
import stat
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.files.downloads import OpenedDownload
from app.models import DownloadLease, User
from app.storage import SharedContentStore

_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)


class ManagedDownloadError(RuntimeError):
    """A managed file no longer matches its authoritative manifest entry."""


class DownloadConcurrencyError(RuntimeError):
    """The durable per-user concurrent download limit was reached."""


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
            user = await self._session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if user is None:
                raise ManagedDownloadError("download owner is missing")
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
            result = await self._session.execute(
                update(DownloadLease)
                .where(DownloadLease.id == lease_id)
                .values(
                    renewed_at=now,
                    expires_at=now + timedelta(seconds=self._lease_seconds),
                )
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise ManagedDownloadError("download lease was lost")

    async def release(self, lease_id: uuid.UUID) -> None:
        await self._session.rollback()
        async with self._session.begin():
            await self._session.execute(delete(DownloadLease).where(DownloadLease.id == lease_id))


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
