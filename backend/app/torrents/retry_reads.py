"""One payload read per storage key in the single-process API runtime."""

import asyncio
import uuid
from collections.abc import Callable
from functools import partial
from typing import Any, cast

from fastapi import Request
from starlette.concurrency import run_in_threadpool


class TorrentRetryBusyError(RuntimeError):
    pass


class TorrentRetryReads:
    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, asyncio.Task[Any]] = {}

    async def run[T](self, key: uuid.UUID, operation: Callable[[], T]) -> T:
        if key in self._tasks:
            raise TorrentRetryBusyError("torrent_retry_in_progress")
        task = asyncio.create_task(run_in_threadpool(operation))
        self._tasks[key] = task
        task.add_done_callback(partial(self._completed, key))
        # Keep admission until the real disk operation ends, even if its caller leaves.
        return await asyncio.shield(task)

    async def wait_idle(self, budget_seconds: float = 2.0) -> bool:
        if not self._tasks:
            return True
        _, pending = await asyncio.wait(set(self._tasks.values()), timeout=budget_seconds)
        return not pending

    def _completed(self, key: uuid.UUID, task: asyncio.Task[Any]) -> None:
        if self._tasks.get(key) is task:
            del self._tasks[key]
        if not task.cancelled():
            task.exception()


def get_torrent_retry_reads(request: Request) -> TorrentRetryReads:
    return cast(TorrentRetryReads, request.app.state.torrent_retry_reads)
