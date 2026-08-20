import errno
import os
import secrets
import stat
import time
import zipfile
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass, field
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
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
CONTROL_DIRECTORY = ".wos-control"
ARCHIVE_DIRECTORY = "archives"
ARCHIVE_DIRECTORY_MODE = 0o700
ARCHIVE_FILE_MODE = 0o600
MAX_ARCHIVE_ENTRIES = 50_000


class ArchiveError(RuntimeError):
    pass


class ArchiveTooLargeError(ArchiveError):
    pass


@dataclass(slots=True)
class OpenedArchive:
    file_descriptor: int
    directory_descriptor: int
    temporary_name: str
    download_name: str
    size: int
    _closed: bool = field(default=False, init=False, repr=False)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        os.close(self.file_descriptor)
        try:
            os.unlink(self.temporary_name, dir_fd=self.directory_descriptor)
        except FileNotFoundError:
            pass
        finally:
            os.close(self.directory_descriptor)

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

    def create(self, username: str, raw_path: str, *, max_source_bytes: int) -> OpenedArchive:
        relative = RelativePath.parse(raw_path)
        if not relative.components:
            raise InvalidRelativePathError("A directory path is required")
        with (
            self._workspace_manager.open_workspace(username) as workspace_fd,
            open_sandboxed_directory(workspace_fd, relative) as source_fd,
        ):
            source_stat = os.fstat(source_fd)
            if not stat.S_ISDIR(source_stat.st_mode):
                raise BrowserPathNotDirectoryError("Path is not a directory")
            archive = self._new_archive(relative.components[-1])
            try:
                self._write_archive(
                    archive.file_descriptor,
                    source_fd,
                    root_name=relative.components[-1],
                    max_source_bytes=max_source_bytes,
                )
                archive.size = os.fstat(archive.file_descriptor).st_size
                os.lseek(archive.file_descriptor, 0, os.SEEK_SET)
                return archive
            except BaseException:
                archive.close()
                raise

    def _new_archive(self, folder_name: str) -> OpenedArchive:
        data_root = self._workspace_manager.data_root
        try:
            data_fd = os.open(data_root, DIRECTORY_OPEN_FLAGS)
        except OSError as exc:
            raise ArchiveError("Archive storage is unavailable") from exc
        try:
            control_fd = self._open_or_create_directory(data_fd, CONTROL_DIRECTORY)
        finally:
            os.close(data_fd)
        try:
            archive_fd = self._open_or_create_directory(control_fd, ARCHIVE_DIRECTORY)
        finally:
            os.close(control_fd)

        for _ in range(10):
            temporary_name = f"archive-{secrets.token_hex(16)}.zip"
            try:
                file_fd = os.open(
                    temporary_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                    ARCHIVE_FILE_MODE,
                    dir_fd=archive_fd,
                )
                return OpenedArchive(
                    file_descriptor=file_fd,
                    directory_descriptor=archive_fd,
                    temporary_name=temporary_name,
                    download_name=f"{folder_name}.zip",
                    size=0,
                )
            except FileExistsError:
                continue
            except OSError as exc:
                os.close(archive_fd)
                raise ArchiveError("Archive storage is unavailable") from exc
        os.close(archive_fd)
        raise ArchiveError("Unable to allocate an archive")

    @staticmethod
    def _open_or_create_directory(parent_fd: int, name: str) -> int:
        try:
            os.mkdir(name, ARCHIVE_DIRECTORY_MODE, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ArchiveError("Archive storage is unavailable") from exc
        try:
            result = os.open(name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise ArchiveError("Archive storage is unsafe") from exc
        inspected = os.fstat(result)
        if not stat.S_ISDIR(inspected.st_mode):
            os.close(result)
            raise ArchiveError("Archive storage is unsafe")
        return result

    def _write_archive(
        self,
        archive_fd: int,
        source_fd: int,
        *,
        root_name: str,
        max_source_bytes: int,
    ) -> None:
        source_bytes = 0
        entries = 0
        with (
            os.fdopen(os.dup(archive_fd), "w+b", closefd=True) as file_object,
            zipfile.ZipFile(
                file_object,
                mode="w",
                compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive,
        ):
            archive.writestr(self._zip_info(f"{root_name}/", os.fstat(source_fd)), b"")
            for directory_fd, relative_name, entry_name, entry_stat in self._walk(source_fd):
                entries += 1
                if entries > MAX_ARCHIVE_ENTRIES:
                    raise ArchiveError("The folder contains too many entries")
                zip_name = f"{root_name}/{relative_name}"
                if stat.S_ISDIR(entry_stat.st_mode):
                    archive.writestr(self._zip_info(f"{zip_name}/", entry_stat), b"")
                    continue
                source_bytes += entry_stat.st_size
                if source_bytes > max_source_bytes:
                    raise ArchiveTooLargeError("The folder is too large to archive")
                self._copy_file(archive, directory_fd, entry_name, zip_name, entry_stat)

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
        parent_fd: int,
        entry_name: str,
        zip_name: str,
        expected: os.stat_result,
    ) -> None:
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
            with archive.open(
                self._zip_info(zip_name, opened), mode="w", force_zip64=True
            ) as target:
                while True:
                    chunk = os.read(file_fd, 1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
        finally:
            os.close(file_fd)


async def stream_archive(
    archive: OpenedArchive,
    *,
    chunk_size: int,
) -> AsyncGenerator[bytes, None]:
    try:
        while True:
            chunk = await run_in_threadpool(os.read, archive.file_descriptor, chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        archive.close()


class ArchiveStreamingResponse(StreamingResponse):
    def __init__(self, archive: OpenedArchive, *, chunk_size: int) -> None:
        self._archive = archive
        super().__init__(
            stream_archive(archive, chunk_size=chunk_size),
            headers={
                "Content-Disposition": archive.content_disposition,
                "Content-Length": str(archive.size),
            },
            media_type="application/zip",
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._archive.close()
