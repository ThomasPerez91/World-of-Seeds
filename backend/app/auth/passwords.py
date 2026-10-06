"""Bound expensive password work independently of filesystem/download threads."""

import asyncio
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import TypeVar

from app.auth.security import hash_password, verify_password

T = TypeVar("T")


class PasswordWorkUnavailableError(Exception):
    """The bounded password budget is full, timed out, or shutting down."""


class PasswordWorkPool:
    def __init__(
        self, *, workers: int = 2, max_pending: int = 10, timeout_seconds: float = 5
    ) -> None:
        if workers < 1 or max_pending < workers or timeout_seconds <= 0:
            raise ValueError("invalid password work limits")
        self._workers = workers
        self._max_pending = max_pending
        self._timeout_seconds = timeout_seconds
        self._lock = Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._pending = 0
        self._closed = False

    def start(self) -> None:
        with self._lock:
            self._closed = False

    def _completed(self, future: Future[T]) -> None:
        # A disconnected/timed-out request still occupies capacity until its actual
        # worker exits. Releasing on coroutine cancellation would bypass the bound.
        with self._lock:
            self._pending -= 1

    async def run(self, operation: Callable[[], T]) -> T:
        with self._lock:
            if self._closed or self._pending >= self._max_pending:
                raise PasswordWorkUnavailableError
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self._workers, thread_name_prefix="wos-password"
                )
            future = self._executor.submit(operation)
            self._pending += 1
        # Install outside the mutex: add_done_callback can execute synchronously.
        future.add_done_callback(self._completed)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await asyncio.wrap_future(future)
        except TimeoutError as exc:
            raise PasswordWorkUnavailableError from exc

    async def aclose(self) -> None:
        with self._lock:
            self._closed = True
            executor, self._executor = self._executor, None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)


# One API process is an enforced deployment invariant. CLI provisioning shares the
# same bounded async entry points; synchronous helpers remain for offline fixtures.
password_work_pool = PasswordWorkPool()


async def hash_password_async(value: str) -> str:
    return await password_work_pool.run(lambda: hash_password(value))


async def verify_password_async(value: str, encoded_hash: str) -> bool:
    return await password_work_pool.run(lambda: verify_password(value, encoded_hash))
