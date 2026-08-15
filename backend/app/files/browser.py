import mimetypes
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.files.directory_sizes import DirectorySizeBudget, DirectorySizeCalculator
from app.files.workspaces import (
    DIRECTORY_OPEN_FLAGS,
    RETIRED_WORKSPACE_DIRECTORIES,
    WorkspaceManager,
)

MAX_PATH_BYTES = 4096
MAX_COMPONENT_BYTES = 255
MAX_DIRECTORY_ENTRIES = 5000


class FileBrowserError(RuntimeError):
    pass


class InvalidRelativePathError(FileBrowserError):
    pass


class BrowserPathNotFoundError(FileBrowserError):
    pass


class BrowserPathBlockedError(FileBrowserError):
    pass


class BrowserPathNotDirectoryError(FileBrowserError):
    pass


class FileEntryKind(StrEnum):
    DIRECTORY = "directory"
    FILE = "file"
    SYMLINK = "symlink"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class RelativePath:
    components: tuple[str, ...]

    @classmethod
    def parse(cls, raw_path: str) -> "RelativePath":
        if len(raw_path.encode("utf-8")) > MAX_PATH_BYTES:
            raise InvalidRelativePathError("Path is too long")
        if raw_path == "":
            return cls(components=())
        if raw_path.startswith(("/", "\\")):
            raise InvalidRelativePathError("Absolute paths are forbidden")

        components = tuple(raw_path.split("/"))
        if any(not is_safe_component(component) for component in components):
            raise InvalidRelativePathError("Path contains a forbidden component")
        return cls(components=components)

    @property
    def value(self) -> str:
        return "/".join(self.components)


def is_safe_component(component: str) -> bool:
    return (
        component not in {"", ".", ".."}
        and "\0" not in component
        and "/" not in component
        and "\\" not in component
        and not any(ord(character) < 32 or ord(character) == 127 for character in component)
        and len(component.encode("utf-8")) <= MAX_COMPONENT_BYTES
    )


@dataclass(frozen=True, slots=True)
class FileEntry:
    name: str
    path: str
    kind: FileEntryKind
    size: int | None
    modified_at: datetime
    media_type: str | None
    blocked: bool


@dataclass(frozen=True, slots=True)
class StorageUsage:
    total: int
    used: int
    available: int


@dataclass(frozen=True, slots=True)
class DirectorySnapshot:
    path: str
    entries: list[FileEntry]
    storage: StorageUsage
    truncated: bool


class SandboxedFileBrowser:
    def __init__(
        self,
        workspace_manager: WorkspaceManager,
        directory_size_calculator: DirectorySizeCalculator | None = None,
    ) -> None:
        self._workspace_manager = workspace_manager
        self._directory_size_calculator = directory_size_calculator or DirectorySizeCalculator()

    def list_directory(self, username: str, raw_path: str) -> DirectorySnapshot:
        relative_path = RelativePath.parse(raw_path)
        with self._workspace_manager.open_workspace(username) as workspace_fd:
            storage = self._storage_usage(workspace_fd)
            with open_sandboxed_directory(workspace_fd, relative_path) as directory_fd:
                entries, truncated = self._list_entries(directory_fd, relative_path)
        return DirectorySnapshot(
            path=relative_path.value,
            entries=entries,
            storage=storage,
            truncated=truncated,
        )

    def _list_entries(
        self,
        directory_fd: int,
        relative_path: RelativePath,
    ) -> tuple[list[FileEntry], bool]:
        entries: list[FileEntry] = []
        truncated = False
        size_budget = DirectorySizeBudget()
        try:
            with os.scandir(directory_fd) as iterator:
                for entry in iterator:
                    if not relative_path.components and entry.name in RETIRED_WORKSPACE_DIRECTORIES:
                        continue
                    if len(entries) >= MAX_DIRECTORY_ENTRIES:
                        truncated = True
                        break
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    kind = entry_kind(entry_stat.st_mode)
                    safe_name = is_safe_component(entry.name)
                    blocked = not safe_name or kind in {
                        FileEntryKind.SYMLINK,
                        FileEntryKind.OTHER,
                    }
                    item_path = "/".join((*relative_path.components, entry.name))
                    size: int | None
                    if kind is FileEntryKind.FILE:
                        size = entry_stat.st_size
                    elif kind is FileEntryKind.DIRECTORY and not blocked:
                        size = self._directory_size_calculator.calculate(
                            directory_fd,
                            entry.name,
                            entry_stat,
                            size_budget,
                        )
                    else:
                        size = None
                    entries.append(
                        FileEntry(
                            name=entry.name,
                            path=item_path,
                            kind=kind,
                            size=size,
                            modified_at=datetime.fromtimestamp(entry_stat.st_mtime, tz=UTC),
                            media_type=(
                                mimetypes.guess_type(entry.name)[0]
                                if kind is FileEntryKind.FILE
                                else None
                            ),
                            blocked=blocked,
                        )
                    )
        except OSError as exc:
            raise BrowserPathBlockedError("Directory cannot be listed safely") from exc

        entries.sort(
            key=lambda item: (entry_sort_order(item.kind), item.name.casefold(), item.name)
        )
        return entries, truncated

    @staticmethod
    def _storage_usage(workspace_fd: int) -> StorageUsage:
        try:
            filesystem = os.fstatvfs(workspace_fd)
        except OSError as exc:
            raise BrowserPathBlockedError("Storage usage is unavailable") from exc
        block_size = filesystem.f_frsize or filesystem.f_bsize
        total = filesystem.f_blocks * block_size
        free = filesystem.f_bfree * block_size
        available = filesystem.f_bavail * block_size
        return StorageUsage(total=total, used=max(total - free, 0), available=available)


@contextmanager
def open_sandboxed_directory(
    workspace_fd: int,
    relative_path: RelativePath,
) -> Iterator[int]:
    """Open a directory path without ever resolving a symbolic link."""

    if relative_path.components and relative_path.components[0] in RETIRED_WORKSPACE_DIRECTORIES:
        raise BrowserPathBlockedError("Retired workspace directories are hidden")

    current_fd = os.dup(workspace_fd)
    try:
        for component in relative_path.components:
            try:
                component_stat = os.stat(
                    component,
                    dir_fd=current_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError as exc:
                raise BrowserPathNotFoundError("Directory does not exist") from exc
            except OSError as exc:
                raise BrowserPathBlockedError("Directory cannot be inspected safely") from exc
            if stat.S_ISLNK(component_stat.st_mode):
                raise BrowserPathBlockedError("Symbolic links cannot be opened")
            if not stat.S_ISDIR(component_stat.st_mode):
                raise BrowserPathNotDirectoryError("Path is not a directory")

            try:
                next_fd = os.open(component, DIRECTORY_OPEN_FLAGS, dir_fd=current_fd)
            except FileNotFoundError as exc:
                raise BrowserPathNotFoundError("Directory does not exist") from exc
            except NotADirectoryError as exc:
                raise BrowserPathBlockedError("Directory changed while opening") from exc
            except OSError as exc:
                raise BrowserPathBlockedError("Directory cannot be opened safely") from exc
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        os.close(current_fd)


def entry_kind(mode: int) -> FileEntryKind:
    if stat.S_ISDIR(mode):
        return FileEntryKind.DIRECTORY
    if stat.S_ISREG(mode):
        return FileEntryKind.FILE
    if stat.S_ISLNK(mode):
        return FileEntryKind.SYMLINK
    return FileEntryKind.OTHER


def entry_sort_order(kind: FileEntryKind) -> int:
    return {
        FileEntryKind.DIRECTORY: 0,
        FileEntryKind.FILE: 1,
        FileEntryKind.SYMLINK: 2,
        FileEntryKind.OTHER: 3,
    }[kind]
