import os
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from app.files.browser import (
    FileEntryKind,
    RelativePath,
    entry_kind,
    open_sandboxed_directory,
)
from app.files.mutations import (
    MutationCompensationError,
    inspect_mutable_source,
    relocate_verified,
    validate_mutable_source_path,
)
from app.files.workspaces import DIRECTORY_OPEN_FLAGS, WorkspaceManager

TRASH_DIRECTORY = ".trash"
TRASH_DIRECTORY_MODE = 0o700
MAX_PURGE_DEPTH = 512


class TrashStorageError(RuntimeError):
    pass


class TrashStorageMissingError(TrashStorageError):
    pass


class TrashStorageUnsafeError(TrashStorageError):
    pass


class TrashPurgeError(TrashStorageError):
    pass


@dataclass(frozen=True, slots=True)
class TrashFilesystemEntry:
    id: uuid.UUID
    original_path: str
    name: str
    kind: FileEntryKind
    size: int | None
    device: int
    inode: int


class TrashFilesystem:
    def __init__(self, data_root: Path, workspace_manager: WorkspaceManager) -> None:
        self._data_root = data_root
        self._workspace_manager = workspace_manager

    def move_to_trash(
        self,
        username: str,
        user_id: uuid.UUID,
        entry_id: uuid.UUID,
        raw_path: str,
    ) -> TrashFilesystemEntry:
        source = validate_mutable_source_path(raw_path)
        source_parent = RelativePath(components=source.components[:-1])
        source_name = source.components[-1]
        storage_name = str(entry_id)

        with (
            self._workspace_manager.open_workspace(username) as workspace_fd,
            open_sandboxed_directory(workspace_fd, source_parent) as source_parent_fd,
            self._trash_user_directory(user_id, create=True) as trash_user_fd,
        ):
            source_stat = inspect_mutable_source(source_parent_fd, source_name)
            source_kind = entry_kind(source_stat.st_mode)
            relocate_verified(
                source_name=source_name,
                destination_name=storage_name,
                source_directory_fd=source_parent_fd,
                destination_directory_fd=trash_user_fd,
                source_stat=source_stat,
            )

        return TrashFilesystemEntry(
            id=entry_id,
            original_path=source.value,
            name=source_name,
            kind=source_kind,
            size=source_stat.st_size if source_kind is FileEntryKind.FILE else None,
            device=source_stat.st_dev,
            inode=source_stat.st_ino,
        )

    def restore(
        self,
        username: str,
        user_id: uuid.UUID,
        entry: TrashFilesystemEntry,
    ) -> None:
        destination = RelativePath.parse(entry.original_path)
        if not destination.components:
            raise TrashStorageUnsafeError("A trash entry cannot target the workspace root")
        destination_parent = RelativePath(components=destination.components[:-1])

        with (
            self._workspace_manager.open_workspace(username) as workspace_fd,
            open_sandboxed_directory(workspace_fd, destination_parent) as destination_parent_fd,
            self._trash_user_directory(user_id, create=False) as trash_user_fd,
        ):
            source_stat = self._inspect_stored_entry(trash_user_fd, entry)
            relocate_verified(
                source_name=str(entry.id),
                destination_name=destination.components[-1],
                source_directory_fd=trash_user_fd,
                destination_directory_fd=destination_parent_fd,
                source_stat=source_stat,
            )

    def restage(
        self,
        username: str,
        user_id: uuid.UUID,
        entry: TrashFilesystemEntry,
    ) -> None:
        source = RelativePath.parse(entry.original_path)
        if not source.components:
            raise TrashStorageUnsafeError("A restored entry has no source path")
        source_parent = RelativePath(components=source.components[:-1])

        with (
            self._workspace_manager.open_workspace(username) as workspace_fd,
            open_sandboxed_directory(workspace_fd, source_parent) as source_parent_fd,
            self._trash_user_directory(user_id, create=True) as trash_user_fd,
        ):
            source_stat = inspect_mutable_source(source_parent_fd, source.components[-1])
            self._verify_identity(source_stat, entry)
            relocate_verified(
                source_name=source.components[-1],
                destination_name=str(entry.id),
                source_directory_fd=source_parent_fd,
                destination_directory_fd=trash_user_fd,
                source_stat=source_stat,
            )

    def purge(self, user_id: uuid.UUID, entry: TrashFilesystemEntry) -> bool:
        try:
            trash_context = self._trash_user_directory(user_id, create=False)
            with trash_context as trash_user_fd:
                try:
                    stored_stat = os.stat(
                        str(entry.id),
                        dir_fd=trash_user_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    return False
                except OSError as exc:
                    raise TrashStorageUnsafeError(
                        "The trash entry cannot be inspected safely"
                    ) from exc

                self._verify_identity(stored_stat, entry)
                if entry.kind is FileEntryKind.FILE:
                    os.unlink(str(entry.id), dir_fd=trash_user_fd)
                    return True

                directory_fd = os.open(str(entry.id), DIRECTORY_OPEN_FLAGS, dir_fd=trash_user_fd)
                try:
                    self._verify_same_inode(os.fstat(directory_fd), stored_stat)
                    self._purge_directory(
                        directory_fd,
                        depth=0,
                        expected_device=stored_stat.st_dev,
                    )
                    current_stat = os.stat(
                        str(entry.id),
                        dir_fd=trash_user_fd,
                        follow_symlinks=False,
                    )
                    self._verify_same_inode(current_stat, stored_stat)
                finally:
                    os.close(directory_fd)
                os.rmdir(str(entry.id), dir_fd=trash_user_fd)
                return True
        except (TrashStorageError, MutationCompensationError):
            raise
        except OSError as exc:
            raise TrashPurgeError("The trash entry could not be permanently deleted") from exc

    @contextmanager
    def _trash_user_directory(self, user_id: uuid.UUID, *, create: bool) -> Iterator[int]:
        try:
            data_fd = os.open(self._data_root, DIRECTORY_OPEN_FLAGS)
        except OSError as exc:
            raise TrashStorageUnsafeError("The configured data root is unavailable") from exc

        trash_fd: int | None = None
        try:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(TRASH_DIRECTORY, mode=TRASH_DIRECTORY_MODE, dir_fd=data_fd)
            try:
                trash_fd = os.open(TRASH_DIRECTORY, DIRECTORY_OPEN_FLAGS, dir_fd=data_fd)
            except FileNotFoundError as exc:
                raise TrashStorageMissingError("The trash root does not exist") from exc
            except OSError as exc:
                raise TrashStorageUnsafeError("The trash root is not a safe directory") from exc
        finally:
            os.close(data_fd)

        user_fd: int | None = None
        user_directory = str(user_id)
        try:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(user_directory, mode=TRASH_DIRECTORY_MODE, dir_fd=trash_fd)
            try:
                user_fd = os.open(user_directory, DIRECTORY_OPEN_FLAGS, dir_fd=trash_fd)
            except FileNotFoundError as exc:
                raise TrashStorageMissingError("The user trash directory does not exist") from exc
            except OSError as exc:
                raise TrashStorageUnsafeError(
                    "The user trash entry is not a safe directory"
                ) from exc
        finally:
            os.close(trash_fd)

        try:
            yield user_fd
        finally:
            os.close(user_fd)

    @staticmethod
    def _inspect_stored_entry(
        trash_user_fd: int,
        entry: TrashFilesystemEntry,
    ) -> os.stat_result:
        try:
            stored_stat = os.stat(
                str(entry.id),
                dir_fd=trash_user_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError as exc:
            raise TrashStorageMissingError("The trash entry is missing") from exc
        except OSError as exc:
            raise TrashStorageUnsafeError("The trash entry cannot be inspected safely") from exc
        TrashFilesystem._verify_identity(stored_stat, entry)
        return stored_stat

    @staticmethod
    def _verify_identity(current: os.stat_result, entry: TrashFilesystemEntry) -> None:
        if (
            current.st_dev != entry.device
            or current.st_ino != entry.inode
            or entry_kind(current.st_mode) is not entry.kind
        ):
            raise TrashStorageUnsafeError("The trash entry identity does not match its metadata")

    @staticmethod
    def _verify_same_inode(current: os.stat_result, expected: os.stat_result) -> None:
        if current.st_dev != expected.st_dev or current.st_ino != expected.st_ino:
            raise TrashStorageUnsafeError("A directory changed during permanent deletion")

    @classmethod
    def _purge_directory(
        cls,
        directory_fd: int,
        *,
        depth: int,
        expected_device: int,
    ) -> None:
        if depth >= MAX_PURGE_DEPTH:
            raise TrashPurgeError("The directory nesting limit was reached")

        try:
            with os.scandir(directory_fd) as iterator:
                for child in iterator:
                    try:
                        child_stat = child.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if child_stat.st_dev != expected_device:
                        raise TrashStorageUnsafeError(
                            "Permanent deletion cannot cross a filesystem boundary"
                        )
                    if stat.S_ISDIR(child_stat.st_mode):
                        child_fd = os.open(child.name, DIRECTORY_OPEN_FLAGS, dir_fd=directory_fd)
                        try:
                            cls._verify_same_inode(os.fstat(child_fd), child_stat)
                            cls._purge_directory(
                                child_fd,
                                depth=depth + 1,
                                expected_device=expected_device,
                            )
                            current_stat = os.stat(
                                child.name,
                                dir_fd=directory_fd,
                                follow_symlinks=False,
                            )
                            cls._verify_same_inode(current_stat, child_stat)
                        finally:
                            os.close(child_fd)
                        os.rmdir(child.name, dir_fd=directory_fd)
                    else:
                        os.unlink(child.name, dir_fd=directory_fd)
        except TrashStorageError:
            raise
        except OSError as exc:
            raise TrashPurgeError("A directory could not be permanently deleted") from exc
