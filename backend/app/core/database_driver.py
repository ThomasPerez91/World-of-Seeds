"""Bound SQLAlchemy's asyncpg awaits, including stalled protocol cancellation.

The adapter extension is tied to the SQLAlchemy version in uv.lock. Its real
PostgreSQL regression tests must pass when upgrading that dependency.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from sqlalchemy.dialects.postgresql.asyncpg import (
    AsyncAdapt_asyncpg_connection,
    AsyncAdapt_asyncpg_dbapi,
)
from sqlalchemy.util.concurrency import await_only

CANCELLATION_GRACE_SECONDS = 1.0


def _consume_result(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        task.exception()


async def bounded_database_wait[T](
    operation: Awaitable[T], *, deadline_seconds: float, terminate: Callable[[], None]
) -> T:
    task = asyncio.ensure_future(operation)
    try:
        done, _ = await asyncio.wait({task}, timeout=deadline_seconds)
        if done:
            return task.result()
        raise TimeoutError("database_operation_deadline")
    finally:
        if not task.done():
            # Terminate synchronously BEFORE waiting for cancellation. A peer that
            # cannot acknowledge cancellation must never hold up its caller.
            terminate()
            task.cancel()
            task.add_done_callback(_consume_result)
            await asyncio.wait({task}, timeout=CANCELLATION_GRACE_SECONDS)


class BoundedAsyncpgConnection(AsyncAdapt_asyncpg_connection):
    __slots__ = ("_operation_deadline",)

    def __init__(self, dbapi: Any, connection: Any, deadline: float, **kwargs: Any) -> None:
        super().__init__(dbapi, connection, **kwargs)  # type: ignore[no-untyped-call]
        self._operation_deadline = deadline

    def await_[T](self, operation: Awaitable[T]) -> T:
        return await_only(
            bounded_database_wait(
                operation,
                deadline_seconds=self._operation_deadline,
                terminate=self.driver_connection.terminate,
            )
        )


class BoundedAsyncpgDBAPI(AsyncAdapt_asyncpg_dbapi):
    def __init__(self, command_timeout: float) -> None:
        super().__init__(asyncpg)  # type: ignore[no-untyped-call]
        self._operation_deadline = command_timeout + CANCELLATION_GRACE_SECONDS

    def connect(self, *args: Any, **kwargs: Any) -> BoundedAsyncpgConnection:
        if kwargs.pop("async_fallback", False):
            raise ValueError("The bounded database driver requires asyncio")
        creator = kwargs.pop("async_creator_fn", asyncpg.connect)
        cache_size = kwargs.pop("prepared_statement_cache_size", 100)
        name_func = kwargs.pop("prepared_statement_name_func", None)
        connection = await_only(creator(*args, **kwargs))
        return BoundedAsyncpgConnection(
            self,
            connection,
            self._operation_deadline,
            prepared_statement_cache_size=cache_size,
            prepared_statement_name_func=name_func,
        )
