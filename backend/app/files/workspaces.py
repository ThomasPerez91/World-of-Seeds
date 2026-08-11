import ctypes
import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Protocol, cast

from app.auth.security import normalize_username

WORKSPACE_DIRECTORIES = ("downloads", "watch")
DIRECTORY_MODE = 0o750
DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
RENAME_NOREPLACE = 1


class WorkspaceError(RuntimeError):
    """Base error for workspace operations."""


class WorkspaceAlreadyExistsError(WorkspaceError):
    pass


class WorkspaceMissingError(WorkspaceError):
    pass


class WorkspaceUnsafeEntryError(WorkspaceError):
    pass


class WorkspaceCompensationError(WorkspaceError):
    pass


class _RenameAt2(Protocol):
    def __call__(
        self,
        old_directory: ctypes.c_int,
        old_name: ctypes.c_char_p,
        new_directory: ctypes.c_int,
        new_name: ctypes.c_char_p,
        flags: ctypes.c_uint,
    ) -> int: ...


def _rename_without_replacement(
    source: str,
    destination: str,
    *,
    directory_fd: int,
) -> None:
    """Atomically rename a workspace without replacing a concurrent destination.

    The production target is Linux. Failing closed when ``renameat2`` is unavailable
    is safer than emulating it with a check followed by ``os.rename``.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename_at2 = cast(_RenameAt2, libc.renameat2)
    except AttributeError as exc:
        raise WorkspaceError("Atomic workspace rename is unavailable") from exc

    result = rename_at2(
        ctypes.c_int(directory_fd),
        ctypes.c_char_p(os.fsencode(source)),
        ctypes.c_int(directory_fd),
        ctypes.c_char_p(os.fsencode(destination)),
        ctypes.c_uint(RENAME_NOREPLACE),
    )
    if result == 0:
        return

    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, os.strerror(error_number), destination)
    raise OSError(error_number, os.strerror(error_number), destination)


class WorkspaceManager:
    """Manage user directories without following symlinks.

    All mutable names are single validated path components. Directory descriptors and
    ``*at`` syscalls keep operations anchored below the configured data root.
    """

    def __init__(self, data_root: Path) -> None:
        self._data_root = data_root

    @staticmethod
    def _validate_name(username: str) -> str:
        try:
            normalized = normalize_username(username)
        except ValueError as exc:
            raise WorkspaceUnsafeEntryError("Invalid workspace name") from exc
        if normalized != username:
            raise WorkspaceUnsafeEntryError("Workspace names must already be normalized")
        return username

    @contextmanager
    def _users_directory(self, *, create: bool) -> Iterator[int]:
        try:
            data_fd = os.open(self._data_root, DIRECTORY_OPEN_FLAGS)
        except OSError as exc:
            raise WorkspaceUnsafeEntryError("The configured data root is unavailable") from exc

        try:
            if create:
                with suppress(FileExistsError):
                    os.mkdir("users", mode=DIRECTORY_MODE, dir_fd=data_fd)
            try:
                users_fd = os.open("users", DIRECTORY_OPEN_FLAGS, dir_fd=data_fd)
            except FileNotFoundError as exc:
                raise WorkspaceMissingError("The users directory does not exist") from exc
            except OSError as exc:
                raise WorkspaceUnsafeEntryError("The users entry is not a safe directory") from exc
        finally:
            os.close(data_fd)

        try:
            yield users_fd
        finally:
            os.close(users_fd)

    @staticmethod
    def _open_workspace(users_fd: int, username: str) -> int:
        try:
            return os.open(username, DIRECTORY_OPEN_FLAGS, dir_fd=users_fd)
        except FileNotFoundError as exc:
            raise WorkspaceMissingError("The user workspace does not exist") from exc
        except OSError as exc:
            raise WorkspaceUnsafeEntryError("The workspace is not a safe directory") from exc

    def create(self, username: str) -> None:
        safe_name = self._validate_name(username)
        with self._users_directory(create=True) as users_fd:
            try:
                os.mkdir(safe_name, mode=DIRECTORY_MODE, dir_fd=users_fd)
            except FileExistsError as exc:
                raise WorkspaceAlreadyExistsError("The user workspace already exists") from exc
            except OSError as exc:
                raise WorkspaceError("Unable to create the user workspace") from exc

            workspace_fd: int | None = None
            created_children: list[str] = []
            try:
                workspace_fd = self._open_workspace(users_fd, safe_name)
                for directory in WORKSPACE_DIRECTORIES:
                    os.mkdir(directory, mode=DIRECTORY_MODE, dir_fd=workspace_fd)
                    created_children.append(directory)
            except BaseException as exc:
                try:
                    if workspace_fd is not None:
                        for directory in reversed(created_children):
                            os.rmdir(directory, dir_fd=workspace_fd)
                    os.rmdir(safe_name, dir_fd=users_fd)
                except OSError as cleanup_error:
                    raise WorkspaceCompensationError(
                        "Unable to clean an incomplete workspace"
                    ) from cleanup_error
                if isinstance(exc, WorkspaceError):
                    raise
                raise WorkspaceError("Unable to initialize the user workspace") from exc
            finally:
                if workspace_fd is not None:
                    os.close(workspace_fd)

    def assert_ready(self, username: str) -> None:
        with self.open_workspace(username) as workspace_fd:
            for directory in WORKSPACE_DIRECTORIES:
                try:
                    child_fd = os.open(directory, DIRECTORY_OPEN_FLAGS, dir_fd=workspace_fd)
                except OSError as exc:
                    raise WorkspaceUnsafeEntryError(
                        "A required workspace directory is unavailable"
                    ) from exc
                os.close(child_fd)

    @contextmanager
    def open_workspace(self, username: str) -> Iterator[int]:
        safe_name = self._validate_name(username)
        with self._users_directory(create=False) as users_fd:
            workspace_fd = self._open_workspace(users_fd, safe_name)
            try:
                yield workspace_fd
            finally:
                os.close(workspace_fd)

    def remove_empty(self, username: str) -> None:
        safe_name = self._validate_name(username)
        with self._users_directory(create=False) as users_fd:
            workspace_fd = self._open_workspace(users_fd, safe_name)
            try:
                entries = set(os.listdir(workspace_fd))
                if entries != set(WORKSPACE_DIRECTORIES):
                    raise WorkspaceCompensationError("The workspace contains unexpected entries")

                for directory in WORKSPACE_DIRECTORIES:
                    child_fd = self._open_child_directory(workspace_fd, directory)
                    try:
                        if os.listdir(child_fd):
                            raise WorkspaceCompensationError("A workspace directory is not empty")
                    finally:
                        os.close(child_fd)

                for directory in reversed(WORKSPACE_DIRECTORIES):
                    os.rmdir(directory, dir_fd=workspace_fd)
            finally:
                os.close(workspace_fd)
            try:
                os.rmdir(safe_name, dir_fd=users_fd)
            except OSError as exc:
                raise WorkspaceCompensationError("Unable to remove the workspace") from exc

    @staticmethod
    def _open_child_directory(workspace_fd: int, name: str) -> int:
        try:
            return os.open(name, DIRECTORY_OPEN_FLAGS, dir_fd=workspace_fd)
        except OSError as exc:
            raise WorkspaceUnsafeEntryError("A workspace child is not a safe directory") from exc

    def rename(self, old_username: str, new_username: str) -> None:
        old_name = self._validate_name(old_username)
        new_name = self._validate_name(new_username)
        if old_name == new_name:
            self.assert_ready(old_name)
            return

        with self._users_directory(create=False) as users_fd:
            try:
                source = os.stat(old_name, dir_fd=users_fd, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise WorkspaceMissingError("The source workspace does not exist") from exc
            if not stat.S_ISDIR(source.st_mode):
                raise WorkspaceUnsafeEntryError("The source workspace is not a directory")

            try:
                os.stat(new_name, dir_fd=users_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise WorkspaceAlreadyExistsError("The destination workspace already exists")

            try:
                _rename_without_replacement(
                    old_name,
                    new_name,
                    directory_fd=users_fd,
                )
            except FileExistsError as exc:
                raise WorkspaceAlreadyExistsError(
                    "The destination workspace already exists"
                ) from exc
            except OSError as exc:
                raise WorkspaceError("Unable to rename the user workspace") from exc

            try:
                destination = os.stat(new_name, dir_fd=users_fd, follow_symlinks=False)
            except OSError as exc:
                self._rollback_rename(users_fd, new_name, old_name)
                raise WorkspaceUnsafeEntryError("The renamed workspace cannot be verified") from exc

            if (
                not stat.S_ISDIR(destination.st_mode)
                or destination.st_dev != source.st_dev
                or destination.st_ino != source.st_ino
            ):
                self._rollback_rename(users_fd, new_name, old_name)
                raise WorkspaceUnsafeEntryError("The workspace changed during rename")

    @staticmethod
    def _rollback_rename(users_fd: int, source: str, destination: str) -> None:
        try:
            _rename_without_replacement(source, destination, directory_fd=users_fd)
        except (OSError, WorkspaceError) as exc:
            raise WorkspaceCompensationError("Unable to roll back workspace rename") from exc

    @contextmanager
    def provision_for_transaction(self, username: str) -> Iterator[None]:
        self.create(username)
        try:
            yield
        except BaseException:
            self.remove_empty(username)
            raise

    @contextmanager
    def rename_for_transaction(
        self,
        old_username: str,
        new_username: str,
    ) -> Iterator[None]:
        self.rename(old_username, new_username)
        renamed = old_username != new_username
        try:
            yield
        except BaseException:
            if renamed:
                try:
                    self.rename(new_username, old_username)
                except WorkspaceError as exc:
                    raise WorkspaceCompensationError(
                        "Unable to restore the original workspace name"
                    ) from exc
            raise
