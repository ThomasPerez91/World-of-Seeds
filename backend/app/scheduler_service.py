from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import socket
import uuid

import httpx

from app.core.config import get_settings
from app.core.database import engine, session_factory
from app.integrations.qbittorrent_v2 import QBittorrentV2Gateway
from app.scheduler.runtime import SchedulerRuntime


def _scheduler_id() -> str:
    hostname = re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname())[:48] or "host"
    return f"scheduler:{hostname}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


async def main() -> None:
    settings = get_settings()
    if (
        settings.qbittorrent_url is None
        or settings.qbittorrent_username is None
        or settings.qbittorrent_password is None
    ):
        raise RuntimeError("V2 scheduler qBittorrent configuration is incomplete")
    timeout = httpx.Timeout(
        connect=settings.integration_connect_timeout_seconds,
        read=settings.integration_read_timeout_seconds,
        write=settings.integration_read_timeout_seconds,
        pool=settings.integration_connect_timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        gateway = QBittorrentV2Gateway(
            client,
            str(settings.qbittorrent_url),
            settings.qbittorrent_username,
            settings.qbittorrent_password.get_secret_value(),
            data_root=settings.qbittorrent_data_root,
        )
        scheduler = SchedulerRuntime(
            session_factory,
            gateway,
            scheduler_id=_scheduler_id(),
        )
        loop = asyncio.get_running_loop()
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, scheduler.request_stop)
        try:
            await scheduler.run()
        finally:
            await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
