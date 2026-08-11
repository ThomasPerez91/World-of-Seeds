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
from app.files.workspaces import WORKSPACE_DIRECTORIES, WorkspaceManager

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


@dataclass(frozen=True, slots=True)
class MutationResult:
    path: str
    name: str
    kind: FileEntryKind


def validate_mutable_source_path(raw_path: str) -> RelativePath:
    source = RelativePath.parse(raw_path)
    if not source.components:
        raise MutationProtectedPathError("The workspace root cannot be changed")
    if len(source.components) == 1 and source.components[0] in WORKSPACE_DIRECTORIES:
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

    def rename(self, username: str, raw_path: str, new_name: str) -> MutationResult:
        source = validate_mutable_source_path(raw_path)
        if not is_safe_component(new_name):
            raise InvalidRelativePathError("The new name is not a safe path component")
        destination = RelativePath(components=(*source.components[:-1], new_name))
        return self._relocate(username, source, destination)

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
        destination: RelativePath,
    ) -> MutationResult:
        if not destination.components:
            raise MutationInvalidTargetError("A destination name is required")

        source_parent = RelativePath(components=source.components[:-1])
        destination_parent = RelativePath(components=destination.components[:-1])
        source_name = source.components[-1]
        destination_name = destination.components[-1]

        with (
            self._workspace_manager.open_workspace(username) as workspace_fd,
            open_sandboxed_directory(workspace_fd, source_parent) as source_parent_fd,
            open_sandboxed_directory(workspace_fd, destination_parent) as destination_parent_fd,
        ):
            source_stat = inspect_mutable_source(source_parent_fd, source_name)
            source_kind = entry_kind(source_stat.st_mode)

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
