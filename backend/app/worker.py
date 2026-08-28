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
from app.core.config import Settings, get_settings, production_secret_is_unsafe
from app.core.database import engine, session_factory
from app.integrations.account_routing import (
    build_deployment_account_router,
    parse_deployment_account_specs,
)
from app.integrations.http import integration_timeout
from app.jobs.torrent_effects import TorrentEffectHandlers, TorrentSyncEnqueuer
from app.jobs.torrent_payloads import MAX_MANAGED_TORRENT_BYTES, TorrentPayloadStore
from app.jobs.worker import TorrentWorker
from app.storage import SharedContentStore


def _worker_id() -> str:
    hostname = re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname())[:48] or "host"
    return f"worker:{hostname}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def validate_worker_runtime(settings: Settings) -> None:
    legacy_values = (
        settings.newgreedy_url,
        settings.qbittorrent_url,
        settings.qbittorrent_username,
        settings.qbittorrent_password,
        settings.c411_passkey,
    )
    if settings.integration_accounts_json is not None:
        if any(value is not None for value in legacy_values):
            raise RuntimeError("v2_worker_integration_config_ambiguous")
        specs = parse_deployment_account_specs(settings.integration_accounts_json)
        if settings.environment == "production" and any(
            production_secret_is_unsafe(secret)
            for spec in specs
            for secret in (
                spec.c411_passkey.get_secret_value(),
                spec.qbittorrent_password.get_secret_value(),
            )
        ):
            raise RuntimeError("v2_worker_integration_secret_invalid")
        return
    if any(value is not None for value in legacy_values):
        raise RuntimeError("v2_worker_integration_registry_required")
    if settings.environment == "production":
        raise RuntimeError("v2_worker_integrations_required")


async def main() -> None:
    settings = get_settings()
    validate_worker_runtime(settings)
    redis = RedisCoordinator.from_settings(settings)
    if settings.integration_accounts_json is None:
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

    timeout = integration_timeout(
        settings.integration_connect_timeout_seconds,
        settings.integration_read_timeout_seconds,
    )
    async with httpx.AsyncClient(timeout=timeout) as client:
        router = build_deployment_account_router(
            settings.integration_accounts_json,
            client,
            session_factory,
            allowed_tracker_hosts=settings.c411_tracker_hosts,
            data_root=settings.qbittorrent_data_root,
            max_total_size=MAX_MANAGED_TORRENT_BYTES,
        )
        payloads = TorrentPayloadStore(
            settings.data_root,
            allowed_tracker_hosts=settings.c411_tracker_hosts,
        )
        effects = TorrentEffectHandlers(
            session_factory,
            router,
            payloads,
            SharedContentStore(settings.data_root),
            redis=redis,
        )
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
