from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import socket
import uuid

from app.coordination import RedisCoordinator
from app.core.config import get_settings
from app.core.database import engine, session_factory
from app.jobs.worker import TorrentWorker


def _worker_id() -> str:
    hostname = re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname())[:48] or "host"
    return f"worker:{hostname}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


async def main() -> None:
    settings = get_settings()
    redis = RedisCoordinator.from_settings(settings)
    worker = TorrentWorker(
        session_factory,
        redis,
        {},
        worker_id=_worker_id(),
    )
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, worker.request_stop)
    try:
        await worker.run()
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
