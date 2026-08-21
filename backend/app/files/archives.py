import errno
import os
import stat
import time
import zipfile
from collections.abc import Generator, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from threading import BoundedSemaphore
from typing import IO, cast
from urllib.parse import quote

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.files.browser import (
    BrowserPathBlockedError,
    BrowserPathNotDirectoryError,
    BrowserPathNotFoundError,
    InvalidRelativePathError,
    RelativePath,
    open_sandboxed_directory,
)
from app.files.workspaces import DIRECTORY_OPEN_FLAGS, WorkspaceManager

FILE_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
MAX_ARCHIVE_ENTRIES = 50_000
_archive_slot = BoundedSemaphore(value=1)


class ArchiveError(RuntimeError):
    pass


class ArchiveTooLargeError(ArchiveError):
    pass


class ArchiveBusyError(ArchiveError):
    pass


class _UnseekableZipBuffer:
    """Small write buffer that lets ``zipfile`` emit a ZIP as a stream."""

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
        raise OSError("Streaming ZIP output is not seekable")

    @staticmethod
    def flush() -> None:
        return None

    def drain(self) -> Iterator[bytes]:
        chunks, self._chunks = self._chunks, []
        yield from chunks


@dataclass(slots=True)
class OpenedFolderArchive:
    source_descriptor: int
    root_name: str
    download_name: str
    _resources: ExitStack = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._resources.close()
        finally:
            _archive_slot.release()

    @property
    def content_disposition(self) -> str:
        fallback = (
            "".join(
                character
                for character in self.download_name
                if character.isascii() and character.isprintable() and character not in {'"', "\\"}
            ).strip()
            or "folder.zip"
        )
        return (
            f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(self.download_name, safe='')}"
        )


class SandboxedFolderArchiver:
    def __init__(self, workspace_manager: WorkspaceManager) -> None:
        self._workspace_manager = workspace_manager

    def open(
        self,
        username: str,
        raw_path: str,
        *,
        max_source_bytes: int,
    ) -> OpenedFolderArchive:
        relative = RelativePath.parse(raw_path)
        if not relative.components:
            raise InvalidRelativePathError("A directory path is required")
        if not _archive_slot.acquire(blocking=False):
            raise ArchiveBusyError("Another folder archive is already running")

        resources = ExitStack()
        try:
            workspace_fd = resources.enter_context(self._workspace_manager.open_workspace(username))
            source_fd = resources.enter_context(open_sandboxed_directory(workspace_fd, relative))
            source_stat = os.fstat(source_fd)
            if not stat.S_ISDIR(source_stat.st_mode):
                raise BrowserPathNotDirectoryError("Path is not a directory")
            self._preflight(source_fd, max_source_bytes=max_source_bytes)
            root_name = relative.components[-1]
            return OpenedFolderArchive(
                source_descriptor=source_fd,
                root_name=root_name,
                download_name=f"{root_name}.zip",
                _resources=resources,
            )
        except BaseException:
            resources.close()
            _archive_slot.release()
            raise

    def _preflight(self, source_fd: int, *, max_source_bytes: int) -> None:
        source_bytes = 0
        for entries, (_, _, _, entry_stat) in enumerate(self._walk(source_fd), start=1):
            if entries > MAX_ARCHIVE_ENTRIES:
                raise ArchiveError("The folder contains too many entries")
            if stat.S_ISREG(entry_stat.st_mode):
                source_bytes += entry_stat.st_size
                if source_bytes > max_source_bytes:
                    raise ArchiveTooLargeError("The folder is too large to archive")

    def stream(
        self,
        opened: OpenedFolderArchive,
        *,
        max_source_bytes: int,
        chunk_size: int,
    ) -> Generator[bytes, None, None]:
        writer = _UnseekableZipBuffer()
        source_bytes = 0
        try:
            with zipfile.ZipFile(
                cast("IO[bytes]", writer),
                mode="w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                archive.writestr(
                    self._zip_info(
                        f"{opened.root_name}/",
                        os.fstat(opened.source_descriptor),
                    ),
                    b"",
                )
                yield from writer.drain()
                for entries, (
                    directory_fd,
                    relative_name,
                    entry_name,
                    entry_stat,
                ) in enumerate(self._walk(opened.source_descriptor), start=1):
                    if entries > MAX_ARCHIVE_ENTRIES:
                        raise ArchiveError("The folder contains too many entries")
                    zip_name = f"{opened.root_name}/{relative_name}"
                    if stat.S_ISDIR(entry_stat.st_mode):
                        archive.writestr(self._zip_info(f"{zip_name}/", entry_stat), b"")
                        yield from writer.drain()
                        continue
                    copied_bytes = yield from self._copy_file(
                        archive,
                        writer,
                        directory_fd,
                        entry_name,
                        zip_name,
                        entry_stat,
                        chunk_size=chunk_size,
                        max_bytes=max_source_bytes - source_bytes,
                    )
                    source_bytes += copied_bytes
            yield from writer.drain()
        finally:
            opened.close()

    def _walk(
        self,
        root_fd: int,
        prefix: str = "",
    ) -> Iterator[tuple[int, str, str, os.stat_result]]:
        try:
            names = sorted(os.listdir(root_fd))
        except OSError as exc:
            raise ArchiveError("The folder cannot be listed safely") from exc
        for name in names:
            if name in {".", ".."} or "/" in name or "\x00" in name:
                raise BrowserPathBlockedError("An archive entry is unsafe")
            try:
                entry_stat = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise BrowserPathNotFoundError("An archive entry disappeared") from exc
            except OSError as exc:
                raise BrowserPathBlockedError("An archive entry cannot be inspected") from exc
            if stat.S_ISLNK(entry_stat.st_mode) or not (
                stat.S_ISDIR(entry_stat.st_mode) or stat.S_ISREG(entry_stat.st_mode)
            ):
                raise BrowserPathBlockedError("Symbolic links and special files cannot be archived")
            relative_name = f"{prefix}/{name}" if prefix else name
            yield root_fd, relative_name, name, entry_stat
            if stat.S_ISDIR(entry_stat.st_mode):
                try:
                    child_fd = os.open(name, DIRECTORY_OPEN_FLAGS, dir_fd=root_fd)
                except OSError as exc:
                    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                        raise BrowserPathBlockedError("An archive directory changed") from exc
                    raise ArchiveError("An archive directory cannot be opened") from exc
                try:
                    opened = os.fstat(child_fd)
                    if opened.st_dev != entry_stat.st_dev or opened.st_ino != entry_stat.st_ino:
                        raise BrowserPathBlockedError("An archive directory changed")
                    yield from self._walk(child_fd, relative_name)
                finally:
                    os.close(child_fd)

    @staticmethod
    def _zip_info(name: str, entry_stat: os.stat_result) -> zipfile.ZipInfo:
        timestamp = max(entry_stat.st_mtime, 315532800)
        info = zipfile.ZipInfo(name, time.localtime(timestamp)[:6])
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = (entry_stat.st_mode & 0xFFFF) << 16
        return info

    def _copy_file(
        self,
        archive: zipfile.ZipFile,
        writer: _UnseekableZipBuffer,
        parent_fd: int,
        entry_name: str,
        zip_name: str,
        expected: os.stat_result,
        *,
        chunk_size: int,
        max_bytes: int,
    ) -> Generator[bytes, None, int]:
        try:
            file_fd = os.open(entry_name, FILE_OPEN_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise BrowserPathBlockedError("An archive file cannot be opened") from exc
        try:
            opened = os.fstat(file_fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != expected.st_dev
                or opened.st_ino != expected.st_ino
            ):
                raise BrowserPathBlockedError("An archive file changed")
            if opened.st_size > max_bytes:
                raise ArchiveTooLargeError("The folder is too large to archive")
            remaining = opened.st_size
            copied_bytes = 0
            with archive.open(
                self._zip_info(zip_name, opened),
                mode="w",
                force_zip64=True,
            ) as target:
                while remaining > 0:
                    chunk = os.read(file_fd, min(chunk_size, remaining))
                    if not chunk:
                        break
                    target.write(chunk)
                    copied_bytes += len(chunk)
                    remaining -= len(chunk)
                    yield from writer.drain()
            yield from writer.drain()
            return copied_bytes
        finally:
            os.close(file_fd)


class ArchiveStreamingResponse(StreamingResponse):
    def __init__(
        self,
        archive: OpenedFolderArchive,
        *,
        archiver: SandboxedFolderArchiver,
        max_source_bytes: int,
        chunk_size: int,
    ) -> None:
        self._archive = archive
        super().__init__(
            archiver.stream(
                archive,
                max_source_bytes=max_source_bytes,
                chunk_size=chunk_size,
            ),
            headers={"Content-Disposition": archive.content_disposition},
            media_type="application/zip",
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._archive.close()
