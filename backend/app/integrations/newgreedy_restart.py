import json
import os
import secrets
import stat
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

from app.integrations.newgreedy_config import (
    CONTROL_DIRECTORY,
    NEWGREEDY_DIRECTORY,
    NewGreedyConfigUnsafeError,
    SecureDirectoryChain,
)

REQUEST_FILENAME = "restart-request.json"
STATUS_FILENAME = "restart-status.json"
STATUS_DIRECTORY = "newgreedy-status"
MAX_CONTROL_FILE_BYTES = 16 * 1024
ACTIVE_STATUS_TTL = timedelta(minutes=5)

type RestartState = Literal["idle", "pending", "restarting", "healthy", "failed", "rejected"]
type RestartMessageCode = Literal[
    "idle",
    "requested",
    "restarting",
    "healthy",
    "restart_failed",
    "cooldown",
    "invalid_request",
]


class NewGreedyRestartError(Exception):
    """Base error for the host-mediated NewGreedy restart channel."""


class NewGreedyRestartUnavailableError(NewGreedyRestartError):
    """Raised when the control directory is not available."""


class NewGreedyRestartUnsafeError(NewGreedyRestartError):
    """Raised when a control file fails an integrity check."""


class NewGreedyRestartPendingError(NewGreedyRestartError):
    """Raised when a restart request already exists."""


@dataclass(frozen=True, slots=True)
class NewGreedyRestartStatus:
    state: RestartState
    request_id: UUID | None
    updated_at: datetime | None
    message_code: RestartMessageCode


class NewGreedyRestartStore:
    def __init__(
        self,
        data_root: Path,
        *,
        status_owner_uid: int = 0,
        request_directory: str = NEWGREEDY_DIRECTORY,
        status_directory: str = STATUS_DIRECTORY,
    ) -> None:
        self._data_root = data_root
        self._status_owner_uid = status_owner_uid
        self._request_directory = request_directory
        self._status_directory = status_directory
        self._lock = Lock()

    def status(self) -> NewGreedyRestartStatus:
        with self._lock:
            pending = self._read_json(
                REQUEST_FILENAME,
                directory=self._control_directory_fd,
                missing_ok=True,
            )
            if pending is not None:
                return _parse_pending_request(pending)
            status_payload = self._read_json(
                STATUS_FILENAME,
                directory=self._status_directory_fd,
                missing_ok=True,
            )
            if status_payload is None:
                return NewGreedyRestartStatus(
                    state="idle",
                    request_id=None,
                    updated_at=None,
                    message_code="idle",
                )
            return _parse_status(status_payload)

    def request(self, requested_by: UUID) -> NewGreedyRestartStatus:
        with self._lock:
            existing_request = self._read_json(
                REQUEST_FILENAME,
                directory=self._control_directory_fd,
                missing_ok=True,
            )
            if existing_request is not None:
                raise NewGreedyRestartPendingError("A restart request is already pending")
            status_payload = self._read_json(
                STATUS_FILENAME,
                directory=self._status_directory_fd,
                missing_ok=True,
            )
            if status_payload is not None:
                current_status = _parse_status(status_payload)
                if (
                    current_status.state == "restarting"
                    and current_status.updated_at is not None
                    and current_status.updated_at >= datetime.now(UTC) - ACTIVE_STATUS_TTL
                ):
                    raise NewGreedyRestartPendingError("A restart is already running")
            request_id = uuid4()
            requested_at = datetime.now(UTC)
            content = json.dumps(
                {
                    "request_id": str(request_id),
                    "requested_at": requested_at.isoformat(),
                    "requested_by": str(requested_by),
                },
                separators=(",", ":"),
            ).encode("utf-8")
            self._create_exclusive(REQUEST_FILENAME, content)
            return NewGreedyRestartStatus(
                state="pending",
                request_id=request_id,
                updated_at=requested_at,
                message_code="requested",
            )

    def _read_json(
        self,
        filename: str,
        *,
        directory: "CallableDirectory",
        missing_ok: bool,
    ) -> object | None:
        try:
            with directory() as directory_fd:
                flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
                try:
                    file_fd = os.open(filename, flags, dir_fd=directory_fd)
                except FileNotFoundError:
                    if missing_ok:
                        return None
                    raise
                try:
                    file_stat = os.fstat(file_fd)
                    if not stat.S_ISREG(file_stat.st_mode):
                        raise NewGreedyRestartUnsafeError("Control file is not regular")
                    if file_stat.st_mode & 0o022:
                        raise NewGreedyRestartUnsafeError("Control file permissions are unsafe")
                    if file_stat.st_uid not in (0, os.geteuid()):
                        raise NewGreedyRestartUnsafeError("Control file ownership is unsafe")
                    if file_stat.st_size > MAX_CONTROL_FILE_BYTES:
                        raise NewGreedyRestartUnsafeError("Control file is too large")
                    chunks: list[bytes] = []
                    remaining = MAX_CONTROL_FILE_BYTES + 1
                    while remaining > 0:
                        chunk = os.read(file_fd, min(4096, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                finally:
                    os.close(file_fd)
        except FileNotFoundError as exc:
            raise NewGreedyRestartUnavailableError("Control directory is missing") from exc
        except PermissionError as exc:
            raise NewGreedyRestartUnavailableError("Control file cannot be read") from exc
        except NewGreedyConfigUnsafeError as exc:
            raise NewGreedyRestartUnsafeError("Control directory is unsafe") from exc
        except OSError as exc:
            raise NewGreedyRestartUnsafeError("Control file path is unsafe") from exc

        raw = b"".join(chunks)
        if len(raw) > MAX_CONTROL_FILE_BYTES:
            raise NewGreedyRestartUnsafeError("Control file is too large")
        try:
            payload: object = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NewGreedyRestartUnsafeError("Control file is not valid JSON") from exc
        return payload

    def _create_exclusive(self, filename: str, content: bytes) -> None:
        temporary_name = f".{filename}.{secrets.token_hex(12)}.tmp"
        temporary_created = False
        try:
            with self._control_directory_fd() as directory_fd:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
                temporary_fd = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=directory_fd,
                )
                temporary_created = True
                try:
                    view = memoryview(content)
                    while view:
                        written = os.write(temporary_fd, view)
                        if written <= 0:
                            raise OSError("Short control file write")
                        view = view[written:]
                    os.fsync(temporary_fd)
                finally:
                    os.close(temporary_fd)
                try:
                    os.link(
                        temporary_name,
                        filename,
                        src_dir_fd=directory_fd,
                        dst_dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError as exc:
                    raise NewGreedyRestartPendingError(
                        "A restart request is already pending"
                    ) from exc
                os.unlink(temporary_name, dir_fd=directory_fd)
                temporary_created = False
                os.fsync(directory_fd)
        except FileNotFoundError as exc:
            raise NewGreedyRestartUnavailableError("Control directory is missing") from exc
        except PermissionError as exc:
            raise NewGreedyRestartUnavailableError("Control directory is not writable") from exc
        except NewGreedyConfigUnsafeError as exc:
            raise NewGreedyRestartUnsafeError("Control directory is unsafe") from exc
        except NewGreedyRestartError:
            raise
        except OSError as exc:
            raise NewGreedyRestartUnsafeError("Restart request could not be created") from exc
        finally:
            if temporary_created:
                try:
                    with self._control_directory_fd() as directory_fd:
                        os.unlink(temporary_name, dir_fd=directory_fd)
                except (OSError, NewGreedyConfigUnsafeError):
                    pass

    def _control_directory_fd(self) -> SecureDirectoryChain:
        return SecureDirectoryChain(
            self._data_root,
            (CONTROL_DIRECTORY, self._request_directory),
        )

    def _status_directory_fd(self) -> "StatusDirectoryChain":
        return StatusDirectoryChain(
            self._data_root,
            owner_uid=self._status_owner_uid,
            status_directory=self._status_directory,
        )


def _parse_pending_request(value: object) -> NewGreedyRestartStatus:
    payload = _object_payload(value)
    return NewGreedyRestartStatus(
        state="pending",
        request_id=_uuid_value(payload.get("request_id")),
        updated_at=_datetime_value(payload.get("requested_at")),
        message_code="requested",
    )


def _parse_status(value: object) -> NewGreedyRestartStatus:
    payload = _object_payload(value)
    state = payload.get("state")
    message_code = payload.get("message_code")
    if state not in ("restarting", "healthy", "failed", "rejected"):
        raise NewGreedyRestartUnsafeError("Restart state is invalid")
    if message_code not in (
        "restarting",
        "healthy",
        "restart_failed",
        "cooldown",
        "invalid_request",
    ):
        raise NewGreedyRestartUnsafeError("Restart message is invalid")
    return NewGreedyRestartStatus(
        state=state,
        request_id=_optional_uuid_value(payload.get("request_id")),
        updated_at=_datetime_value(payload.get("updated_at")),
        message_code=message_code,
    )


def _object_payload(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise NewGreedyRestartUnsafeError("Control payload is invalid")
    return value


def _uuid_value(value: object) -> UUID:
    if not isinstance(value, str):
        raise NewGreedyRestartUnsafeError("Restart identifier is invalid")
    try:
        return UUID(value)
    except ValueError as exc:
        raise NewGreedyRestartUnsafeError("Restart identifier is invalid") from exc


def _optional_uuid_value(value: object) -> UUID | None:
    if value is None:
        return None
    return _uuid_value(value)


def _datetime_value(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise NewGreedyRestartUnsafeError("Restart timestamp is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NewGreedyRestartUnsafeError("Restart timestamp is invalid") from exc
    if result.tzinfo is None:
        raise NewGreedyRestartUnsafeError("Restart timestamp has no timezone")
    return result.astimezone(UTC)


type CallableDirectory = Callable[[], AbstractContextManager[int]]


class StatusDirectoryChain:
    def __init__(self, data_root: Path, *, owner_uid: int, status_directory: str) -> None:
        self._data_root = data_root
        self._owner_uid = owner_uid
        self._status_directory = status_directory
        self._fd: int | None = None

    def __enter__(self) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        current_fd = os.open(self._data_root, flags)
        try:
            self._validate_application_directory(current_fd)
            for component in (CONTROL_DIRECTORY,):
                next_fd = os.open(component, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
                self._validate_application_directory(current_fd)
            next_fd = os.open(self._status_directory, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
            self._validate_status_directory(current_fd)
            self._fd = current_fd
            return current_fd
        except Exception:
            os.close(current_fd)
            raise

    def __exit__(self, *_: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    @staticmethod
    def _validate_application_directory(directory_fd: int) -> None:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise NewGreedyRestartUnsafeError("Control path is not a directory")
        if directory_stat.st_mode & 0o022 or directory_stat.st_uid != os.geteuid():
            raise NewGreedyRestartUnsafeError("Control directory is unsafe")

    def _validate_status_directory(self, directory_fd: int) -> None:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise NewGreedyRestartUnsafeError("Status path is not a directory")
        if directory_stat.st_mode & 0o022 or directory_stat.st_uid != self._owner_uid:
            raise NewGreedyRestartUnsafeError("Status directory is unsafe")
        if directory_stat.st_gid != os.getegid():
            raise NewGreedyRestartUnsafeError("Status directory group is unsafe")
