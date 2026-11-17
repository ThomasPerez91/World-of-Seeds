"""Liveness file for the worker and scheduler containers.

A background task refreshes the file's modification time every few seconds. It runs on the
same event loop as the service, so a loop blocked by synchronous work or a deadlocked process
stops refreshing it, and the container healthcheck reports the container unhealthy:

    python -c "import os, sys, time; sys.exit(time.time() - os.stat(PATH).st_mtime > 120)"
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = Path("/tmp/wos-heartbeat")
HEARTBEAT_INTERVAL_SECONDS = 15.0


def touch(path: Path) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    try:
        os.utime(descriptor)
    finally:
        os.close(descriptor)


async def _beat(path: Path, interval: float) -> None:
    warned = False
    while True:
        try:
            touch(path)
        except OSError:
            # A missing /tmp only makes the container unhealthy; it must not stop the service.
            if not warned:
                logger.warning("runtime_heartbeat_unavailable", exc_info=True)
                warned = True
        else:
            warned = False
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def runtime_heartbeat(
    path: Path = HEARTBEAT_PATH,
    *,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> AsyncIterator[None]:
    """Keep the liveness file fresh for as long as the service body runs."""
    if interval <= 0:
        raise ValueError("heartbeat interval must be positive")
    task = asyncio.create_task(_beat(path, interval), name="runtime-heartbeat")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
