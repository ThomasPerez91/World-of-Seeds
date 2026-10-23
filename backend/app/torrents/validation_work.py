"""Bound torrent parsing and staging independently of download/filesystem threads."""

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event, Lock
from typing import TypeVar

T = TypeVar("T")


class TorrentValidationBusyError(RuntimeError):
    """The technical torrent-work budget is full or shutting down."""


class TorrentValidationPool:
    def __init__(self, *, workers: int = 2, max_pending: int = 4) -> None:
        if workers < 1 or max_pending < workers:
            raise ValueError("invalid torrent work limits")
        self._workers = workers
        self._max_pending = max_pending
        self._lock = Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._pending = 0
        self._closed = False

    def start(self) -> None:
        with self._lock:
            self._closed = False

    def _completed(self, future: Future[T]) -> None:
        with self._lock:
            self._pending -= 1

    async def run(self, operation: Callable[[], T]) -> T:
        abandoned = Event()

        def execute() -> T:
            if abandoned.is_set():
                raise TorrentValidationBusyError
            return operation()

        with self._lock:
            if self._closed or self._pending >= self._max_pending:
                raise TorrentValidationBusyError
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._workers, thread_name_prefix="wos-torrent"
                )
            future = self._executor.submit(execute)
            self._pending += 1
        # Register outside the lock: an already completed future calls back inline.
        future.add_done_callback(self._completed)
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(lambda result: None if result.cancelled() else result.exception())
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # Running work retains its slot until the real thread exits. Cancelled
            # queued entries also keep their budget until dequeued, bounding memory.
            abandoned.set()
            raise

    async def aclose(self) -> None:
        with self._lock:
            self._closed = True
            executor, self._executor = self._executor, None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)


torrent_validation_pool = TorrentValidationPool()
