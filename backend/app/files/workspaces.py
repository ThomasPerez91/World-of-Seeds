import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.auth.security import normalize_username
from app.files.atomic import AtomicRenameUnavailableError, rename_without_replacement

WORKSPACE_DIRECTORIES = ("downloads", "watch")
LEGACY_USERS_DIRECTORY = "users"
DIRECTORY_MODE = 0o750
DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


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


def _rename_without_replacement(
    source: str,
    destination: str,
    *,
    source_directory_fd: int,
    destination_directory_fd: int,
) -> None:
    """Atomically rename without replacing an existing destination.

    The production target is Linux. Failing closed when ``renameat2`` is unavailable
    is safer than emulating it with a check followed by ``os.rename``.
    """

    try:
        rename_without_replacement(
            source,
            destination,
            source_directory_fd=source_directory_fd,
            destination_directory_fd=destination_directory_fd,
        )
    except AtomicRenameUnavailableError as exc:
        raise WorkspaceError("Atomic workspace rename is unavailable") from exc


class WorkspaceManager:
    """Manage direct ``/data/<username>`` workspaces without following symlinks."""

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
    def _data_directory(self) -> Iterator[int]:
        try:
            data_fd = os.open(self._data_root, DIRECTORY_OPEN_FLAGS)
        except OSError as exc:
            raise WorkspaceUnsafeEntryError("The configured data root is unavailable") from exc
        try:
            yield data_fd
        finally:
            os.close(data_fd)

    @staticmethod
    def _open_directory(parent_fd: int, name: str, *, missing_message: str) -> int:
        try:
            return os.open(name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise WorkspaceMissingError(missing_message) from exc
        except OSError as exc:
            raise WorkspaceUnsafeEntryError("The workspace entry is not a safe directory") from exc

    @staticmethod
    def _inspect_directory(
        parent_fd: int,
        name: str,
        *,
        missing_message: str,
    ) -> os.stat_result:
        try:
            result = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise WorkspaceMissingError(missing_message) from exc
        except OSError as exc:
            raise WorkspaceUnsafeEntryError("The workspace cannot be inspected safely") from exc
        if not stat.S_ISDIR(result.st_mode):
            raise WorkspaceUnsafeEntryError("The workspace entry is not a directory")
        return result

    @classmethod
    def _assert_ready_fd(cls, workspace_fd: int) -> None:
        for directory in WORKSPACE_DIRECTORIES:
            child_fd = cls._open_directory(
                workspace_fd,
                directory,
                missing_message="A required workspace directory is missing",
            )
            os.close(child_fd)

    def create(self, username: str) -> None:
        safe_name = self._validate_name(username)
        with self._data_directory() as data_fd:
            try:
                os.mkdir(safe_name, mode=DIRECTORY_MODE, dir_fd=data_fd)
            except FileExistsError as exc:
                raise WorkspaceAlreadyExistsError("The user workspace already exists") from exc
            except OSError as exc:
                raise WorkspaceError("Unable to create the user workspace") from exc

            workspace_fd: int | None = None
            created_children: list[str] = []
            try:
                workspace_fd = self._open_directory(
                    data_fd,
                    safe_name,
                    missing_message="The newly created workspace is missing",
                )
                for directory in WORKSPACE_DIRECTORIES:
                    os.mkdir(directory, mode=DIRECTORY_MODE, dir_fd=workspace_fd)
                    created_children.append(directory)
            except BaseException as exc:
                try:
                    if workspace_fd is not None:
                        for directory in reversed(created_children):
                            os.rmdir(directory, dir_fd=workspace_fd)
                    os.rmdir(safe_name, dir_fd=data_fd)
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
            self._assert_ready_fd(workspace_fd)

    @contextmanager
    def open_workspace(self, username: str) -> Iterator[int]:
        safe_name = self._validate_name(username)
        with self._data_directory() as data_fd:
            workspace_fd = self._open_directory(
                data_fd,
                safe_name,
                missing_message="The user workspace does not exist",
            )
            try:
                yield workspace_fd
            finally:
                os.close(workspace_fd)

    def remove_empty(self, username: str) -> None:
        safe_name = self._validate_name(username)
        with self._data_directory() as data_fd:
            workspace_fd = self._open_directory(
                data_fd,
                safe_name,
                missing_message="The user workspace does not exist",
            )
            try:
                entries = set(os.listdir(workspace_fd))
                if entries != set(WORKSPACE_DIRECTORIES):
                    raise WorkspaceCompensationError("The workspace contains unexpected entries")

                for directory in WORKSPACE_DIRECTORIES:
                    child_fd = self._open_directory(
                        workspace_fd,
                        directory,
                        missing_message="A required workspace directory is missing",
                    )
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
                os.rmdir(safe_name, dir_fd=data_fd)
            except OSError as exc:
                raise WorkspaceCompensationError("Unable to remove the workspace") from exc

    def rename(self, old_username: str, new_username: str) -> None:
        old_name = self._validate_name(old_username)
        new_name = self._validate_name(new_username)
        if old_name == new_name:
            self.assert_ready(old_name)
            return

        with self._data_directory() as data_fd:
            source = self._inspect_directory(
                data_fd,
                old_name,
                missing_message="The source workspace does not exist",
            )
            try:
                os.stat(new_name, dir_fd=data_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise WorkspaceAlreadyExistsError("The destination workspace already exists")

            try:
                _rename_without_replacement(
                    old_name,
                    new_name,
                    source_directory_fd=data_fd,
                    destination_directory_fd=data_fd,
                )
            except FileExistsError as exc:
                raise WorkspaceAlreadyExistsError(
                    "The destination workspace already exists"
                ) from exc
            except OSError as exc:
                raise WorkspaceError("Unable to rename the user workspace") from exc

            try:
                destination = self._inspect_directory(
                    data_fd,
                    new_name,
                    missing_message="The renamed workspace is missing",
                )
            except WorkspaceError as exc:
                self._rollback_rename(data_fd, new_name, data_fd, old_name)
                raise WorkspaceUnsafeEntryError("The renamed workspace cannot be verified") from exc

            if destination.st_dev != source.st_dev or destination.st_ino != source.st_ino:
                self._rollback_rename(data_fd, new_name, data_fd, old_name)
                raise WorkspaceUnsafeEntryError("The workspace changed during rename")

    def migrate_legacy(self, username: str) -> bool:
        """Move ``/data/users/<username>`` to ``/data/<username>`` exactly once."""

        safe_name = self._validate_name(username)
        with self._data_directory() as data_fd:
            try:
                self._inspect_directory(
                    data_fd,
                    safe_name,
                    missing_message="The direct workspace does not exist",
                )
                direct_exists = True
            except WorkspaceMissingError:
                direct_exists = False

            try:
                legacy_users_fd = self._open_directory(
                    data_fd,
                    LEGACY_USERS_DIRECTORY,
                    missing_message="The legacy users directory does not exist",
                )
            except WorkspaceMissingError:
                if not direct_exists:
                    raise WorkspaceMissingError("No workspace exists for this user") from None
                self.assert_ready(safe_name)
                return False

            try:
                try:
                    legacy_stat = self._inspect_directory(
                        legacy_users_fd,
                        safe_name,
                        missing_message="The legacy workspace does not exist",
                    )
                    legacy_exists = True
                except WorkspaceMissingError:
                    legacy_stat = None
                    legacy_exists = False

                if direct_exists and legacy_exists:
                    raise WorkspaceAlreadyExistsError(
                        "Both direct and legacy workspaces exist for this user"
                    )
                if direct_exists:
                    self.assert_ready(safe_name)
                    return False
                if not legacy_exists or legacy_stat is None:
                    raise WorkspaceMissingError("No workspace exists for this user")

                legacy_workspace_fd = self._open_directory(
                    legacy_users_fd,
                    safe_name,
                    missing_message="The legacy workspace does not exist",
                )
                try:
                    self._assert_ready_fd(legacy_workspace_fd)
                finally:
                    os.close(legacy_workspace_fd)

                try:
                    _rename_without_replacement(
                        safe_name,
                        safe_name,
                        source_directory_fd=legacy_users_fd,
                        destination_directory_fd=data_fd,
                    )
                except FileExistsError as exc:
                    raise WorkspaceAlreadyExistsError(
                        "The direct workspace appeared during migration"
                    ) from exc
                except OSError as exc:
                    raise WorkspaceError("Unable to migrate the legacy workspace") from exc

                try:
                    migrated = self._inspect_directory(
                        data_fd,
                        safe_name,
                        missing_message="The migrated workspace is missing",
                    )
                except WorkspaceError as exc:
                    self._rollback_rename(data_fd, safe_name, legacy_users_fd, safe_name)
                    raise WorkspaceUnsafeEntryError(
                        "The migrated workspace cannot be verified"
                    ) from exc
                if migrated.st_dev != legacy_stat.st_dev or migrated.st_ino != legacy_stat.st_ino:
                    self._rollback_rename(data_fd, safe_name, legacy_users_fd, safe_name)
                    raise WorkspaceUnsafeEntryError("The workspace changed during migration")
                return True
            finally:
                os.close(legacy_users_fd)

    def remove_legacy_root_if_empty(self) -> bool:
        """Remove only an empty, real ``/data/users`` directory."""

        with self._data_directory() as data_fd:
            try:
                users_fd = self._open_directory(
                    data_fd,
                    LEGACY_USERS_DIRECTORY,
                    missing_message="The legacy users directory does not exist",
                )
            except WorkspaceMissingError:
                return False
            try:
                if os.listdir(users_fd):
                    return False
            finally:
                os.close(users_fd)
            try:
                os.rmdir(LEGACY_USERS_DIRECTORY, dir_fd=data_fd)
            except OSError as exc:
                raise WorkspaceError("Unable to remove the empty legacy directory") from exc
            return True

    @staticmethod
    def _rollback_rename(
        source_directory_fd: int,
        source: str,
        destination_directory_fd: int,
        destination: str,
    ) -> None:
        try:
            _rename_without_replacement(
                source,
                destination,
                source_directory_fd=source_directory_fd,
                destination_directory_fd=destination_directory_fd,
            )
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
