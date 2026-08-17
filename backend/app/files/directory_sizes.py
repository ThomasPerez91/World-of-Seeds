import os
import stat
import time
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock

from app.files.workspaces import DIRECTORY_OPEN_FLAGS

MAX_SIZE_SCAN_ENTRIES = 50_000
MAX_SIZE_SCAN_DEPTH = 128
DIRECTORY_SIZE_CACHE_SECONDS = 30.0
MAX_DIRECTORY_SIZE_CACHE_ENTRIES = 512


@dataclass(slots=True)
class DirectorySizeBudget:
    remaining_entries: int = MAX_SIZE_SCAN_ENTRIES

    def consume(self) -> bool:
        if self.remaining_entries <= 0:
            return False
        self.remaining_entries -= 1
        return True


@dataclass(frozen=True, slots=True)
class _CachedSize:
    size: int | None
    expires_at: float


class DirectorySizeCalculator:
    """Bounded, symlink-safe directory size calculation with a short-lived cache."""

    def __init__(
        self,
        *,
        cache_seconds: float = DIRECTORY_SIZE_CACHE_SECONDS,
        max_cache_entries: int = MAX_DIRECTORY_SIZE_CACHE_ENTRIES,
    ) -> None:
        if cache_seconds <= 0 or max_cache_entries <= 0:
            raise ValueError("Directory size cache limits must be positive")
        self._cache_seconds = cache_seconds
        self._max_cache_entries = max_cache_entries
        self._cache: OrderedDict[tuple[int, int, int, int], _CachedSize] = OrderedDict()
        self._cache_lock = Lock()

    def calculate(
        self,
        parent_fd: int,
        name: str,
        expected: os.stat_result,
        budget: DirectorySizeBudget,
    ) -> int | None:
        cache_key = (
            expected.st_dev,
            expected.st_ino,
            expected.st_mtime_ns,
            expected.st_ctime_ns,
        )
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached.size

        directory_fd: int | None = None
        try:
            directory_fd = os.open(name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
            current = os.fstat(directory_fd)
            if current.st_dev != expected.st_dev or current.st_ino != expected.st_ino:
                return None
            size = self._scan(
                directory_fd,
                depth=0,
                expected_device=expected.st_dev,
                budget=budget,
            )
        except OSError:
            size = None
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

        self._store(cache_key, size)
        return size

    def _scan(
        self,
        directory_fd: int,
        *,
        depth: int,
        expected_device: int,
        budget: DirectorySizeBudget,
    ) -> int | None:
        if depth >= MAX_SIZE_SCAN_DEPTH:
            return None

        total = 0
        with os.scandir(directory_fd) as iterator:
            for child in iterator:
                if not budget.consume():
                    return None
                try:
                    child_stat = child.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if child_stat.st_dev != expected_device:
                    return None
                if stat.S_ISREG(child_stat.st_mode):
                    total += child_stat.st_size
                    continue
                if not stat.S_ISDIR(child_stat.st_mode):
                    continue

                child_fd = os.open(child.name, DIRECTORY_OPEN_FLAGS, dir_fd=directory_fd)
                try:
                    opened = os.fstat(child_fd)
                    if opened.st_dev != child_stat.st_dev or opened.st_ino != child_stat.st_ino:
                        return None
                    child_size = self._scan(
                        child_fd,
                        depth=depth + 1,
                        expected_device=expected_device,
                        budget=budget,
                    )
                finally:
                    os.close(child_fd)
                if child_size is None:
                    return None
                total += child_size
        return total

    def _get_cached(self, key: tuple[int, int, int, int]) -> _CachedSize | None:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is None:
                return None
            if cached.expires_at <= now:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return cached

    def _store(self, key: tuple[int, int, int, int], size: int | None) -> None:
        with self._cache_lock:
            self._cache[key] = _CachedSize(
                size=size,
                expires_at=time.monotonic() + self._cache_seconds,
            )
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_entries:
                self._cache.popitem(last=False)
