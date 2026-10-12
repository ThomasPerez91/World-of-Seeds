"""One non-queued reconciliation disk operation per API application."""

import asyncio
from collections.abc import Callable
from typing import Any, cast

from fastapi import Request
from starlette.concurrency import run_in_threadpool


class ReconciliationBusyError(RuntimeError):
    pass


class ReconciliationDiskReads:
    def __init__(self) -> None:
        self._task: asyncio.Task[Any] | None = None

    async def run[T](self, operation: Callable[[], T]) -> T:
        if self._task is not None:
            raise ReconciliationBusyError("reconciliation_busy")
        task = asyncio.create_task(run_in_threadpool(operation))
        self._task = task
        task.add_done_callback(self._completed)
        # Cancellation cannot stop an OS read already running in a thread. Keep
        # admission occupied until that real operation completes, not its caller.
        return await asyncio.shield(task)

    async def wait_idle(self, budget_seconds: float = 2.0) -> bool:
        task = self._task
        if task is None:
            return True
        done, _ = await asyncio.wait({task}, timeout=budget_seconds)
        return bool(done)

    def _completed(self, task: asyncio.Task[Any]) -> None:
        if self._task is task:
            self._task = None
        if not task.cancelled():
            task.exception()


def get_reconciliation_disk_reads(request: Request) -> ReconciliationDiskReads:
    return cast(ReconciliationDiskReads, request.app.state.reconciliation_disk_reads)
