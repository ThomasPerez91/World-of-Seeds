import os
import stat
from dataclasses import dataclass

from app.files.atomic import AtomicRenameUnavailableError, rename_without_replacement
from app.files.browser import (
    BrowserPathBlockedError,
    BrowserPathNotFoundError,
    FileBrowserError,
    FileEntryKind,
    InvalidRelativePathError,
    RelativePath,
    entry_kind,
    is_safe_component,
    open_sandboxed_directory,
)
from app.files.workspaces import (
    PROTECTED_WORKSPACE_DIRECTORIES,
    RETIRED_WORKSPACE_DIRECTORIES,
    WorkspaceManager,
)

__all__ = ["rename_without_replacement"]


class FileMutationError(FileBrowserError):
    pass


class MutationCollisionError(FileMutationError):
    pass


class MutationInvalidTargetError(FileMutationError):
    pass


class MutationProtectedPathError(FileMutationError):
    pass


class MutationUnsupportedTypeError(FileMutationError):
    pass


class MutationCompensationError(FileMutationError):
    pass


COMPOUND_EXTENSIONS = frozenset(
    {
        ".tar.gz",
        ".tar.bz2",
        ".tar.xz",
        ".tar.zst",
        ".user.js",
    }
)


@dataclass(frozen=True, slots=True)
class MutationResult:
    path: str
    name: str
    kind: FileEntryKind


def split_file_name(name: str) -> tuple[str, str]:
    """Return the editable basename and protected extension for a regular file."""

    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    lowered = name.lower()
    for extension in sorted(COMPOUND_EXTENSIONS, key=len, reverse=True):
        if lowered.endswith(extension) and len(name) > len(extension):
            return name[: -len(extension)], name[-len(extension) :]
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        return name, ""
    return name[:dot], name[dot:]


def validate_mutable_source_path(raw_path: str) -> RelativePath:
    source = RelativePath.parse(raw_path)
    if not source.components:
        raise MutationProtectedPathError("The workspace root cannot be changed")
    if source.components[0] in RETIRED_WORKSPACE_DIRECTORIES:
        raise MutationProtectedPathError("Retired workspace directories are hidden")
    if len(source.components) == 1 and source.components[0] in PROTECTED_WORKSPACE_DIRECTORIES:
        raise MutationProtectedPathError("Required workspace directories cannot be changed")
    return source


def inspect_mutable_source(parent_fd: int, name: str) -> os.stat_result:
    try:
        source_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise BrowserPathNotFoundError("The source does not exist") from exc
    except OSError as exc:
        raise BrowserPathBlockedError("The source cannot be inspected safely") from exc

    if stat.S_ISLNK(source_stat.st_mode):
        raise BrowserPathBlockedError("Symbolic links cannot be changed")
    if not (stat.S_ISREG(source_stat.st_mode) or stat.S_ISDIR(source_stat.st_mode)):
        raise MutationUnsupportedTypeError("Only files and directories can be changed")
    return source_stat


def rollback_relocation(
    *,
    source_name: str,
    destination_name: str,
    source_directory_fd: int,
    destination_directory_fd: int,
) -> None:
    try:
        rename_without_replacement(
            source_name,
            destination_name,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
        )
    except (OSError, AtomicRenameUnavailableError) as exc:
        raise MutationCompensationError(
            "The file mutation could not be rolled back safely"
        ) from exc


def relocate_verified(
    *,
    source_name: str,
    destination_name: str,
    source_directory_fd: int,
    destination_directory_fd: int,
    source_stat: os.stat_result,
) -> None:
    try:
        rename_without_replacement(
            source_name,
            destination_name,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
        )
    except FileExistsError as exc:
        raise MutationCollisionError("The destination already exists") from exc
    except AtomicRenameUnavailableError as exc:
        raise FileMutationError("Atomic file mutation is unavailable") from exc
    except FileNotFoundError as exc:
        raise BrowserPathNotFoundError("The source no longer exists") from exc
    except OSError as exc:
        raise FileMutationError("The entry could not be moved safely") from exc

    try:
        destination_stat = os.stat(
            destination_name,
            dir_fd=destination_directory_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        rollback_relocation(
            source_name=destination_name,
            destination_name=source_name,
            source_directory_fd=destination_directory_fd,
            destination_directory_fd=source_directory_fd,
        )
        raise BrowserPathBlockedError(
            "The moved entry could not be verified and was restored"
        ) from exc

    if (
        destination_stat.st_dev != source_stat.st_dev
        or destination_stat.st_ino != source_stat.st_ino
        or entry_kind(destination_stat.st_mode) is not entry_kind(source_stat.st_mode)
    ):
        rollback_relocation(
            source_name=destination_name,
            destination_name=source_name,
            source_directory_fd=destination_directory_fd,
            destination_directory_fd=source_directory_fd,
        )
        raise BrowserPathBlockedError("The source changed during the operation")


class SandboxedFileMutator:
    def __init__(self, workspace_manager: WorkspaceManager) -> None:
        self._workspace_manager = workspace_manager

    def rename(self, username: str, raw_path: str, new_basename: str) -> MutationResult:
        source = validate_mutable_source_path(raw_path)
        if not is_safe_component(new_basename):
            raise InvalidRelativePathError("The new name is not a safe path component")
        return self._relocate(username, source, None, new_basename=new_basename)

    def create_directory(self, username: str, raw_parent: str, name: str) -> MutationResult:
        parent = RelativePath.parse(raw_parent)
        if not is_safe_component(name):
            raise InvalidRelativePathError("The directory name is not a safe path component")
        with (
            self._workspace_manager.open_workspace(username) as workspace_fd,
            open_sandboxed_directory(workspace_fd, parent) as parent_fd,
        ):
            try:
                os.mkdir(name, mode=0o750, dir_fd=parent_fd)
            except FileExistsError as exc:
                raise MutationCollisionError("The directory already exists") from exc
            except OSError as exc:
                raise FileMutationError("The directory could not be created safely") from exc
            try:
                created = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as exc:
                raise MutationCompensationError("The directory could not be verified") from exc
            if not stat.S_ISDIR(created.st_mode):
                raise MutationCompensationError("The created entry is not a directory")
        destination = RelativePath(components=(*parent.components, name))
        return MutationResult(path=destination.value, name=name, kind=FileEntryKind.DIRECTORY)

    def move(
        self,
        username: str,
        raw_path: str,
        raw_destination_directory: str,
    ) -> MutationResult:
        source = validate_mutable_source_path(raw_path)
        destination_directory = RelativePath.parse(raw_destination_directory)
        destination = RelativePath(
            components=(*destination_directory.components, source.components[-1])
        )
        return self._relocate(username, source, destination)

    def _relocate(
        self,
        username: str,
        source: RelativePath,
        destination: RelativePath | None,
        *,
        new_basename: str | None = None,
    ) -> MutationResult:
        if destination is not None and not destination.components:
            raise MutationInvalidTargetError("A destination name is required")

        source_parent = RelativePath(components=source.components[:-1])
        source_name = source.components[-1]
        destination_parent = (
            source_parent
            if destination is None
            else RelativePath(components=destination.components[:-1])
        )

        with (
            self._workspace_manager.open_workspace(username) as workspace_fd,
            open_sandboxed_directory(workspace_fd, source_parent) as source_parent_fd,
            open_sandboxed_directory(workspace_fd, destination_parent) as destination_parent_fd,
        ):
            source_stat = inspect_mutable_source(source_parent_fd, source_name)
            source_kind = entry_kind(source_stat.st_mode)

            if new_basename is not None:
                extension = ""
                if source_kind is FileEntryKind.FILE:
                    _, extension = split_file_name(source_name)
                destination_name = f"{new_basename}{extension}"
                if len(destination_name) > 255 or not is_safe_component(destination_name):
                    raise InvalidRelativePathError("The new name is not a safe path component")
                destination = RelativePath(components=(*source.components[:-1], destination_name))
            elif destination is not None:
                destination_name = destination.components[-1]
            else:
                raise MutationInvalidTargetError("A destination name is required")

            if source.components == destination.components:
                return MutationResult(
                    path=source.value,
                    name=source_name,
                    kind=source_kind,
                )

            if source_kind is FileEntryKind.DIRECTORY and self._is_same_or_descendant(
                destination_parent,
                source,
            ):
                raise MutationInvalidTargetError(
                    "A directory cannot be moved into itself or one of its descendants"
                )

            relocate_verified(
                source_name=source_name,
                destination_name=destination_name,
                source_directory_fd=source_parent_fd,
                destination_directory_fd=destination_parent_fd,
                source_stat=source_stat,
            )

        return MutationResult(
            path=destination.value,
            name=destination_name,
            kind=source_kind,
        )

    @staticmethod
    def _is_same_or_descendant(candidate: RelativePath, parent: RelativePath) -> bool:
        return candidate.components[: len(parent.components)] == parent.components
