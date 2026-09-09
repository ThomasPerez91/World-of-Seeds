import errno
import hashlib
import mimetypes
import os
import stat
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from app.files.browser import (
    BrowserPathBlockedError,
    BrowserPathNotFoundError,
    FileBrowserError,
    InvalidRelativePathError,
    RelativePath,
    open_sandboxed_directory,
)
from app.files.workspaces import WorkspaceManager

DOWNLOAD_CHUNK_SIZE = 1024 * 1024
FILE_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)


class DownloadPathNotFileError(FileBrowserError):
    pass


class RangeNotSatisfiableError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ByteRange:
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass(slots=True)
class OpenedDownload:
    file_descriptor: int
    name: str
    size: int
    modified_at: datetime
    media_type: str
    etag: str
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.close(self.file_descriptor)

    @property
    def last_modified(self) -> str:
        return format_datetime(self.modified_at, usegmt=True)

    @property
    def content_disposition(self) -> str:
        ascii_name = "".join(
            character
            for character in self.name
            if character.isascii() and character.isprintable() and character not in {'"', "\\"}
        ).strip()
        fallback = ascii_name or "download"
        encoded_name = quote(self.name, safe="")
        return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded_name}"


class SandboxedFileDownloader:
    def __init__(self, workspace_manager: WorkspaceManager) -> None:
        self._workspace_manager = workspace_manager

    def open(self, username: str, raw_path: str) -> OpenedDownload:
        relative_path = RelativePath.parse(raw_path)
        if not relative_path.components:
            raise InvalidRelativePathError("A file path is required")

        parent_path = RelativePath(components=relative_path.components[:-1])
        file_name = relative_path.components[-1]
        with (
            self._workspace_manager.open_workspace(username) as workspace_fd,
            open_sandboxed_directory(workspace_fd, parent_path) as parent_fd,
        ):
            return self._open_file(parent_fd, file_name)

    @staticmethod
    def _open_file(parent_fd: int, file_name: str) -> OpenedDownload:
        try:
            entry_stat = os.stat(file_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise BrowserPathNotFoundError("File does not exist") from exc
        except OSError as exc:
            raise BrowserPathBlockedError("File cannot be inspected safely") from exc
        if stat.S_ISLNK(entry_stat.st_mode):
            raise BrowserPathBlockedError("Symbolic links cannot be downloaded")
        if not stat.S_ISREG(entry_stat.st_mode):
            raise DownloadPathNotFileError("Path is not a regular file")

        try:
            file_fd = os.open(file_name, FILE_OPEN_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise BrowserPathNotFoundError("File does not exist") from exc
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise BrowserPathBlockedError("File changed while opening") from exc
            raise BrowserPathBlockedError("File cannot be opened safely") from exc

        try:
            opened_stat = os.fstat(file_fd)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise DownloadPathNotFileError("Path is not a regular file")
            modified_at = datetime.fromtimestamp(opened_stat.st_mtime, tz=UTC).replace(
                microsecond=0
            )
            etag_source = (
                f"{opened_stat.st_dev}:{opened_stat.st_ino}:"
                f"{opened_stat.st_size}:{opened_stat.st_mtime_ns}"
            )
            etag = f'"{hashlib.sha256(etag_source.encode()).hexdigest()[:32]}"'
            return OpenedDownload(
                file_descriptor=file_fd,
                name=file_name,
                size=opened_stat.st_size,
                modified_at=modified_at,
                media_type=mimetypes.guess_type(file_name)[0] or "application/octet-stream",
                etag=etag,
            )
        except BaseException:
            os.close(file_fd)
            raise


def parse_range_header(header_value: str, file_size: int) -> ByteRange:
    unit, separator, raw_range = header_value.partition("=")
    if separator == "" or unit.strip().lower() != "bytes":
        raise RangeNotSatisfiableError("Only byte ranges are supported")

    range_spec = raw_range.strip()
    if range_spec.count(",") != 0:
        raise RangeNotSatisfiableError("Multiple ranges are not supported")
    start_text, dash, end_text = range_spec.partition("-")
    if dash == "" or "-" in end_text or (start_text == "" and end_text == ""):
        raise RangeNotSatisfiableError("Invalid byte range")
    if not _is_bounded_number(start_text) or not _is_bounded_number(end_text):
        raise RangeNotSatisfiableError("Invalid byte range")
    if file_size == 0:
        raise RangeNotSatisfiableError("An empty file has no satisfiable range")

    if start_text == "":
        suffix_length = int(end_text)
        if suffix_length == 0:
            raise RangeNotSatisfiableError("Suffix length must be positive")
        return ByteRange(start=max(file_size - suffix_length, 0), end=file_size - 1)

    start = int(start_text)
    if start >= file_size:
        raise RangeNotSatisfiableError("Range starts after the file")
    end = file_size - 1 if end_text == "" else min(int(end_text), file_size - 1)
    if end < start:
        raise RangeNotSatisfiableError("Range end precedes its start")
    return ByteRange(start=start, end=end)


def _is_bounded_number(value: str) -> bool:
    return value == "" or (value.isascii() and value.isdigit() and len(value) <= 20)


def if_range_matches(header_value: str | None, download: OpenedDownload) -> bool:
    if header_value is None:
        return True
    candidate = header_value.strip()
    if candidate.startswith('"'):
        return candidate == download.etag
    if candidate.startswith("W/"):
        return False
    try:
        parsed_date = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        return False
    if parsed_date.tzinfo is None:
        parsed_date = parsed_date.replace(tzinfo=UTC)
    return download.modified_at <= parsed_date.astimezone(UTC)


async def stream_download(
    download: OpenedDownload,
    *,
    start: int,
    length: int,
    chunk_size: int = DOWNLOAD_CHUNK_SIZE,
) -> AsyncGenerator[bytes, None]:
    if chunk_size <= 0:
        raise ValueError("Download chunk size must be positive")
    offset = start
    remaining = length
    try:
        while remaining > 0:
            requested = min(remaining, chunk_size)
            chunk = await run_in_threadpool(
                os.pread,
                download.file_descriptor,
                requested,
                offset,
            )
            if not chunk:
                break
            yield chunk
            offset += len(chunk)
            remaining -= len(chunk)
    finally:
        download.close()


class DownloadStreamingResponse(StreamingResponse):
    """Close the file even if sending headers fails before iteration starts."""

    def __init__(
        self,
        download: OpenedDownload,
        *,
        start: int,
        length: int,
        status_code: int,
        headers: dict[str, str],
        chunk_size: int = DOWNLOAD_CHUNK_SIZE,
    ) -> None:
        self._download = download
        super().__init__(
            stream_download(download, start=start, length=length, chunk_size=chunk_size),
            status_code=status_code,
            headers=headers,
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._download.close()
