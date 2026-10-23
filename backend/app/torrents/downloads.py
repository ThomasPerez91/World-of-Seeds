from __future__ import annotations

import asyncio
import errno
import hashlib
import mimetypes
import os
import stat
import sys
import time
import uuid
import zipfile
from collections.abc import AsyncGenerator, Awaitable, Callable, Generator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import PurePosixPath
from typing import IO, Literal, cast
from urllib.parse import quote

import anyio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from app.http_downloads import OpenedDownload
from app.models import (
    DatabaseOption,
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.storage import SharedContentStore
from app.torrents.traffic import DownloadTrafficScheduler

_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)


class _ManagedArchiveConcurrency:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active = 0
        self._waiters: list[object] = []

    async def acquire(self, limit: int) -> None:
        if not 1 <= limit <= 16:
            raise ValueError("managed archive concurrency is invalid")
        ticket = object()
        async with self._condition:
            self._waiters.append(ticket)
            try:
                await self._condition.wait_for(
                    lambda: self._waiters[0] is ticket and self._active < limit
                )
            except BaseException:
                if ticket in self._waiters:
                    self._waiters.remove(ticket)
                self._condition.notify_all()
                raise
            self._waiters.pop(0)
            self._active += 1
            self._condition.notify_all()

    async def release(self) -> None:
        async with self._condition:
            if self._active <= 0:
                raise RuntimeError("managed archive concurrency underflow")
            self._active -= 1
            self._condition.notify_all()


_managed_archive_concurrency = _ManagedArchiveConcurrency()


class ManagedDownloadError(RuntimeError):
    """A managed file no longer matches its authoritative manifest entry."""


class DownloadConcurrencyError(RuntimeError):
    """The durable per-user concurrent download limit was reached."""


@dataclass(frozen=True, slots=True)
class ManagedArchiveEntry:
    relative_path: str
    size: int
    file_index: int
    archive_path: str | None = None


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
        max_concurrent_global: int = 1,
    ) -> None:
        self._downloader = downloader
        self._storage_key = storage_key
        self._entries = entries
        self._manifest_checksum = manifest_checksum
        self._manifest_version = manifest_version
        self._download_name = download_name
        self._max_concurrent_global = max_concurrent_global
        self._acquired = False

    async def acquire(self) -> None:
        if self._acquired:
            return
        await _managed_archive_concurrency.acquire(self._max_concurrent_global)
        self._acquired = True

    async def release(self) -> None:
        if not self._acquired:
            return
        self._acquired = False
        await _managed_archive_concurrency.release()

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
                        entry.archive_path or entry.relative_path,
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


async def finish_download(*operations: Callable[[], Awaitable[None]]) -> None:
    """Finish every resource cleanup, shield cancellation and preserve the cause."""
    primary = sys.exception()

    async def cleanup() -> None:
        first: BaseException | None = None
        for operation in operations:
            try:
                await operation()
            except BaseException as error:
                if first is None:
                    first = error
        if first is not None:
            raise first

    # Starlette can cancel through an AnyIO scope, callers through Task.cancel().
    with anyio.CancelScope(shield=True):
        task = asyncio.create_task(cleanup())
        cancellation: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                cancellation = error
            except BaseException:
                break
        try:
            task.result()
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note(f"Download cleanup also failed: {type(error).__name__}")
        if cancellation is not None and primary is None:
            raise cancellation


class DownloadLeaseManager:
    def __init__(
        self,
        session: AsyncSession,
        *,
        lease_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        heartbeat_wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("download lease duration is invalid")
        self._session = session
        self._lease_seconds = lease_seconds
        self._heartbeat_wait = heartbeat_wait
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
        max_concurrent: int | None,
        max_concurrent_global: int | None = 20,
        kind: Literal["file", "archive"] = "file",
    ) -> DownloadLease:
        if max_concurrent is not None and not 1 <= max_concurrent <= 20:
            raise ValueError("download concurrency limit is invalid")
        if max_concurrent_global is not None and not 1 <= max_concurrent_global <= 20:
            raise ValueError("global download concurrency limit is invalid")
        lease_id: uuid.UUID | None = None
        try:
            async with self._session.begin():
                global_lock = await self._session.scalar(
                    select(DatabaseOption)
                    .where(DatabaseOption.key == "WOS_DOWNLOAD_MAX_CONCURRENT_GLOBAL")
                    .with_for_update()
                )
                if global_lock is None:
                    raise ManagedDownloadError("global download concurrency option is missing")
                owner = await self._session.scalar(
                    select(User)
                    .where(User.id == user_id)
                    # Serialize account changes while allowing FK KEY SHARE checks
                    # from expiration, which already holds the torrent parent.
                    .with_for_update(key_share=True)
                    .execution_options(populate_existing=True)
                )
                if owner is None or not owner.is_active or owner.deleted_at is not None:
                    raise ManagedDownloadError("download owner is missing or inactive")
                # Same parent-before-right order as expiration and cancellation.
                managed = await self._session.get(
                    ManagedTorrent,
                    managed_torrent_id,
                    with_for_update=True,
                    populate_existing=True,
                )
                request = await self._session.scalar(
                    select(TorrentRequest)
                    .where(
                        TorrentRequest.id == torrent_request_id,
                        TorrentRequest.user_id == user_id,
                        TorrentRequest.managed_torrent_id == managed_torrent_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                file_id = await self._session.scalar(
                    select(TorrentFile.id)
                    .where(
                        TorrentFile.id == torrent_file_id,
                        TorrentFile.managed_torrent_id == managed_torrent_id,
                    )
                    .with_for_update()
                )
                now = self._clock()
                if (
                    managed is None
                    or managed.state is not ManagedTorrentState.READY
                    or request is None
                    or request.state is not TorrentRequestState.READY
                    or request.unsubscribe_at is None
                    or _as_utc(request.unsubscribe_at) <= _as_utc(now)
                    or file_id is None
                ):
                    raise ManagedDownloadError("download right is no longer ready")
                await self._session.execute(
                    delete(DownloadLease).where(
                        DownloadLease.expires_at <= now,
                    )
                )
                if max_concurrent_global is not None:
                    active_global = await self._session.scalar(
                        select(func.count())
                        .select_from(DownloadLease)
                        .where(DownloadLease.expires_at > now, DownloadLease.kind == kind)
                    )
                    if active_global is None or active_global >= max_concurrent_global:
                        raise DownloadConcurrencyError("global download concurrency limit reached")
                if max_concurrent is not None:
                    active = await self._session.scalar(
                        select(func.count())
                        .select_from(DownloadLease)
                        .where(
                            DownloadLease.user_id == user_id,
                            DownloadLease.expires_at > now,
                            DownloadLease.kind == kind,
                        )
                    )
                    if active is None or active >= max_concurrent:
                        raise DownloadConcurrencyError("download concurrency limit reached")
                lease = DownloadLease(
                    user_id=user_id,
                    managed_torrent_id=managed_torrent_id,
                    torrent_request_id=torrent_request_id,
                    torrent_file_id=torrent_file_id,
                    kind=kind,
                    expires_at=now + timedelta(seconds=self._lease_seconds),
                    created_at=now,
                    renewed_at=now,
                )
                self._session.add(lease)
                await self._session.flush()
                lease_id = lease.id
        except BaseException:
            # A cancelled commit may have reached SQL; use the pre-commit scalar
            # to explicitly clean up without touching rollback-expired ORM state.
            if lease_id is not None:
                await finish_download(partial(self.release, lease_id))
            raise
        return lease

    async def renew(self, lease_id: uuid.UUID) -> None:
        async with self._session.begin():
            candidate = await self._session.get(DownloadLease, lease_id)
            if candidate is None:
                raise ManagedDownloadError("download lease was lost")
            user = await self._session.get(
                User, candidate.user_id, with_for_update={"key_share": True}, populate_existing=True
            )
            managed = await self._session.get(
                ManagedTorrent,
                candidate.managed_torrent_id,
                with_for_update=True,
                populate_existing=True,
            )
            request = await self._session.get(
                TorrentRequest,
                candidate.torrent_request_id,
                with_for_update=True,
                populate_existing=True,
            )
            lease = await self._session.get(
                DownloadLease, lease_id, with_for_update=True, populate_existing=True
            )
            now = self._clock()
            owner_can_download = user is not None and user.is_active and user.deleted_at is None
            ready_right = (
                managed is not None
                and managed.state is ManagedTorrentState.READY
                and request is not None
                and request.state is TorrentRequestState.READY
            )
            finishing_expired_download = (
                managed is not None
                and managed.state
                in {
                    ManagedTorrentState.READY,
                    ManagedTorrentState.PURGE_PENDING,
                }
                and request is not None
                and request.state is TorrentRequestState.EXPIRED
            )
            finishing_cancelled_download = (
                managed is not None
                and managed.state
                in {
                    ManagedTorrentState.READY,
                    ManagedTorrentState.PURGE_PENDING,
                }
                and request is not None
                and request.state is TorrentRequestState.CANCELLED
            )
            can_finish = ready_right or finishing_expired_download or finishing_cancelled_download
            if (
                lease is None
                or _as_utc(lease.expires_at) <= _as_utc(now)
                or not owner_can_download
                or not can_finish
            ):
                raise ManagedDownloadError("download lease was lost")
            lease.renewed_at = now
            lease.expires_at = now + timedelta(seconds=self._lease_seconds)

    async def keep_alive(self, lease_id: uuid.UUID) -> None:
        """Renew on independent short SQL sessions even while ASGI send is blocked."""
        while True:
            await self._heartbeat_wait(self.heartbeat_seconds)
            await self.renew_independent(lease_id)

    async def renew_independent(self, lease_id: uuid.UUID) -> None:
        async with AsyncSession(bind=self._session.bind, expire_on_commit=False) as session:
            await DownloadLeaseManager(
                session, lease_seconds=self._lease_seconds, clock=self._clock
            ).renew(lease_id)

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
        self._stream_next: dict[uuid.UUID, float] = {}

    async def reserve(
        self,
        user_id: uuid.UUID,
        byte_count: int,
        *,
        per_user_bytes_per_second: int,
        global_bytes_per_second: int,
        stream_id: uuid.UUID | None = None,
        stream_bytes_per_second: int = 0,
    ) -> float:
        if (
            byte_count < 0
            or per_user_bytes_per_second < 0
            or global_bytes_per_second < 0
            or stream_bytes_per_second < 0
        ):
            raise ValueError("download rate values must be non-negative")
        if stream_bytes_per_second and stream_id is None:
            raise ValueError("stream rate requires a stream ID")
        async with self._lock:
            now = self._clock()
            user_ready = self._user_next.get(user_id, now)
            global_ready = self._global_next
            stream_ready = self._stream_next.get(stream_id, now) if stream_id else now
            ready_at = max(
                now,
                user_ready if per_user_bytes_per_second else now,
                global_ready if global_bytes_per_second else now,
                stream_ready if stream_bytes_per_second else now,
            )
            if per_user_bytes_per_second:
                self._user_next[user_id] = ready_at + byte_count / per_user_bytes_per_second
            if global_bytes_per_second:
                self._global_next = ready_at + byte_count / global_bytes_per_second
            if stream_id is not None and stream_bytes_per_second:
                self._stream_next[stream_id] = ready_at + byte_count / stream_bytes_per_second
            if len(self._user_next) > 10_000:
                self._user_next = {
                    key: value for key, value in self._user_next.items() if value > now
                }
            if len(self._stream_next) > 10_000:
                self._stream_next = {
                    key: value for key, value in self._stream_next.items() if value > now
                }
            return max(0.0, ready_at - now)


async def stream_managed_download(
    download: OpenedDownload,
    *,
    start: int,
    length: int,
    chunk_size: int,
    user_id: uuid.UUID,
    lease_id: uuid.UUID,
    leases: DownloadLeaseManager,
    limiter: DownloadRateLimiter,
    traffic: DownloadTrafficScheduler,
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
    try:
        while remaining > 0:
            requested, share = await traffic.next_chunk(
                lease_id,
                min(remaining, chunk_size),
            )
            delay = await limiter.reserve(
                user_id,
                requested,
                per_user_bytes_per_second=per_user_bytes_per_second,
                global_bytes_per_second=global_bytes_per_second,
                stream_id=lease_id,
                stream_bytes_per_second=share,
            )
            while delay > 0:
                interval = min(delay, leases.heartbeat_seconds)
                await asyncio.sleep(interval)
                delay -= interval
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


async def _read_archive_chunk(chunks: Iterator[bytes]) -> bytes | None:
    # A cancelled coroutine cannot stop an already running filesystem thread.
    # Drain it before closing its generator or releasing the archive slot.
    task = asyncio.create_task(run_in_threadpool(_next_archive_chunk, chunks))
    try:
        return await asyncio.shield(task)
    finally:
        if not task.done():

            async def drain() -> None:
                await task

            await finish_download(drain)


async def stream_managed_archive(
    archiver: ManagedFolderArchiver,
    *,
    chunk_size: int,
    user_id: uuid.UUID,
    leases: DownloadLeaseManager,
    limiter: DownloadRateLimiter,
    per_user_bytes_per_second: int,
    global_bytes_per_second: int,
) -> AsyncGenerator[bytes, None]:
    chunks = archiver.iter_chunks(chunk_size=chunk_size)
    try:
        while True:
            chunk = await _read_archive_chunk(chunks)
            if chunk is None:
                return
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
            yield chunk
    finally:
        await finish_download(partial(run_in_threadpool, chunks.close))


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
        traffic: DownloadTrafficScheduler,
        per_user_bytes_per_second: int,
        global_bytes_per_second: int,
    ) -> None:
        self._download = download
        self._lease_id = lease.id
        self._leases = leases
        self._traffic = traffic
        self._expected_length = length
        super().__init__(
            stream_managed_download(
                download,
                start=start,
                length=length,
                chunk_size=chunk_size,
                user_id=user_id,
                lease_id=self._lease_id,
                leases=leases,
                limiter=limiter,
                traffic=traffic,
                per_user_bytes_per_second=per_user_bytes_per_second,
                global_bytes_per_second=global_bytes_per_second,
            ),
            status_code=status_code,
            headers=headers,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        sent = 0
        outcome: Literal["completed", "interrupted", "error"] = "interrupted"

        async def measured_send(message: Message) -> None:
            nonlocal sent
            await send(message)
            if message["type"] == "http.response.body":
                size = len(message.get("body", b""))
                sent += size
                await self._traffic.report(self._lease_id, size)

        try:
            async with asyncio.TaskGroup() as group:
                heartbeat = group.create_task(self._leases.keep_alive(self._lease_id))
                try:
                    await super().__call__(scope, receive, measured_send)
                finally:
                    heartbeat.cancel()
            if sent == self._expected_length:
                outcome = "completed"
        except asyncio.CancelledError:
            raise
        except BaseException:
            outcome = "error"
            raise
        finally:
            self._download.close()
            await finish_download(
                partial(self._traffic.unregister, self._lease_id, outcome=outcome),
                partial(self._leases.release, self._lease_id),
            )


class ManagedArchiveStreamingResponse(StreamingResponse):
    def __init__(
        self,
        archiver: ManagedFolderArchiver,
        *,
        chunk_size: int,
        user_id: uuid.UUID,
        admit: Callable[[], Awaitable[uuid.UUID]],
        leases: DownloadLeaseManager,
        limiter: DownloadRateLimiter,
        per_user_bytes_per_second: int,
        global_bytes_per_second: int,
    ) -> None:
        self._archiver = archiver
        self._admit = admit
        self._lease_id: uuid.UUID | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self._leases = leases
        self._body = stream_managed_archive(
            archiver,
            chunk_size=chunk_size,
            user_id=user_id,
            leases=leases,
            limiter=limiter,
            per_user_bytes_per_second=per_user_bytes_per_second,
            global_bytes_per_second=global_bytes_per_second,
        )
        super().__init__(
            self._body,
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": archiver.content_disposition,
            },
            media_type="application/zip",
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        version = tuple(map(int, scope.get("asgi", {}).get("spec_version", "2.0").split(".")))

        async def checked_send(message: Message) -> None:
            try:
                await send(message)
            except OSError as error:
                if version >= (2, 4):
                    raise ClientDisconnect from error
                raise

        async def stream() -> None:
            await self._archiver.acquire()
            # Recheck after the queue, before sending even the success headers.
            assert self._lease_id is not None
            await self._leases.renew_independent(self._lease_id)
            await self.stream_response(checked_send)

        try:
            # SQL admission stays BEFORE the FIFO, under this response's lifetime.
            # No background task exists on an uncalled/discarded response.
            self._lease_id = await self._admit()
            async with asyncio.TaskGroup() as group:
                heartbeat = group.create_task(self._leases.keep_alive(self._lease_id))
                self._heartbeat = heartbeat
                transfer = group.create_task(stream())
                disconnected = group.create_task(self.listen_for_disconnect(receive))
                await asyncio.wait((transfer, disconnected), return_when=asyncio.FIRST_COMPLETED)
                if not transfer.done():
                    transfer.cancel()
                disconnected.cancel()
                heartbeat.cancel()
        except BaseExceptionGroup as error:
            # Match StreamingResponse's single-error semantics; keep genuine
            # simultaneous independent failures grouped.
            cause: BaseException = error
            while isinstance(cause, BaseExceptionGroup) and len(cause.exceptions) == 1:
                cause = cause.exceptions[0]
            if cause is not error:
                raise cause from cause.__cause__
            raise
        finally:
            operations: list[Callable[[], Awaitable[None]]] = [
                self._body.aclose,
                self._archiver.release,
            ]
            if self._lease_id is not None:
                operations.append(partial(self._leases.release, self._lease_id))
            await finish_download(*operations)
