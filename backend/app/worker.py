from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import socket
import uuid

import httpx

from app.coordination import RedisCoordinator
from app.core.config import get_settings
from app.core.database import engine, session_factory
from app.integrations.c411_v2 import C411NewGreedyV2Gateway, NewGreedyV2Gateway
from app.integrations.http import integration_timeout
from app.integrations.qbittorrent_v2 import QBittorrentV2Gateway
from app.jobs.torrent_effects import TorrentEffectHandlers, TorrentSyncEnqueuer
from app.jobs.torrent_payloads import MAX_MANAGED_TORRENT_BYTES, TorrentPayloadStore
from app.jobs.worker import TorrentWorker


def _worker_id() -> str:
    hostname = re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname())[:48] or "host"
    return f"worker:{hostname}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


async def main() -> None:
    settings = get_settings()
    integration_values = (
        settings.newgreedy_url,
        settings.qbittorrent_url,
        settings.qbittorrent_username,
        settings.qbittorrent_password,
        settings.c411_passkey,
    )
    if any(value is not None for value in integration_values) and not all(
        value is not None for value in integration_values
    ):
        raise RuntimeError("V2 worker integration configuration is incomplete")
    redis = RedisCoordinator.from_settings(settings)
    if not any(value is not None for value in integration_values):
        worker = TorrentWorker(session_factory, redis, {}, worker_id=_worker_id())
        loop = asyncio.get_running_loop()
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, worker.request_stop)
        try:
            await worker.run()
        finally:
            await redis.aclose()
            await engine.dispose()
        return

    assert settings.newgreedy_url is not None
    assert settings.qbittorrent_url is not None
    assert settings.qbittorrent_username is not None
    assert settings.qbittorrent_password is not None
    assert settings.c411_passkey is not None
    timeout = integration_timeout(
        settings.integration_connect_timeout_seconds,
        settings.integration_read_timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        qbittorrent = QBittorrentV2Gateway(
            client,
            str(settings.qbittorrent_url),
            settings.qbittorrent_username,
            settings.qbittorrent_password.get_secret_value(),
            data_root=settings.qbittorrent_data_root,
        )
        adder = C411NewGreedyV2Gateway(
            qbittorrent,
            NewGreedyV2Gateway(client, str(settings.newgreedy_url)),
            passkey=settings.c411_passkey,
            allowed_tracker_hosts=settings.c411_tracker_hosts,
            max_total_size=MAX_MANAGED_TORRENT_BYTES,
        )
        payloads = TorrentPayloadStore(
            settings.data_root,
            allowed_tracker_hosts=settings.c411_tracker_hosts,
        )
        effects = TorrentEffectHandlers(session_factory, adder, qbittorrent, payloads)
        worker = TorrentWorker(
            session_factory,
            redis,
            effects.handlers,
            worker_id=_worker_id(),
        )
        sync_enqueuer = TorrentSyncEnqueuer(session_factory, redis)
        loop = asyncio.get_running_loop()

        def request_stop() -> None:
            worker.request_stop()
            sync_enqueuer.request_stop()

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, request_stop)
        try:
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(worker.run())
                tasks.create_task(sync_enqueuer.run())
        finally:
            await redis.aclose()
            await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
