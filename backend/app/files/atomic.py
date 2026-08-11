import ctypes
import errno
import os
from typing import Protocol, cast

RENAME_NOREPLACE = 1


class AtomicRenameUnavailableError(RuntimeError):
    """Raised when Linux cannot provide an atomic no-replace rename."""


class _RenameAt2(Protocol):
    def __call__(
        self,
        old_directory: ctypes.c_int,
        old_name: ctypes.c_char_p,
        new_directory: ctypes.c_int,
        new_name: ctypes.c_char_p,
        flags: ctypes.c_uint,
    ) -> int: ...


def rename_without_replacement(
    source: str,
    destination: str,
    *,
    source_directory_fd: int,
    destination_directory_fd: int,
) -> None:
    """Atomically rename an entry without replacing an existing destination.

    World of Seeds targets Linux. Failing closed when ``renameat2`` is unavailable
    is safer than emulating it with a racy existence check followed by ``rename``.
    """

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename_at2 = cast(_RenameAt2, libc.renameat2)
    except AttributeError as exc:
        raise AtomicRenameUnavailableError("Atomic no-replace rename is unavailable") from exc

    result = rename_at2(
        ctypes.c_int(source_directory_fd),
        ctypes.c_char_p(os.fsencode(source)),
        ctypes.c_int(destination_directory_fd),
        ctypes.c_char_p(os.fsencode(destination)),
        ctypes.c_uint(RENAME_NOREPLACE),
    )
    if result == 0:
        return

    error_number = ctypes.get_errno()
    if error_number in {errno.ENOSYS, errno.EOPNOTSUPP}:
        raise AtomicRenameUnavailableError("Atomic no-replace rename is unavailable")
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), destination)
    raise OSError(error_number, os.strerror(error_number), destination)
