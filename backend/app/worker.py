from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import socket
import uuid
from contextlib import suppress

import httpx
from sqlalchemy.exc import SQLAlchemyError

from app.coordination import RedisCoordinator
from app.core.config import Settings, get_settings, production_secret_is_unsafe
from app.core.database import engine, session_factory
from app.integrations.account_routing import (
    build_deployment_account_router,
    parse_deployment_account_specs,
)
from app.integrations.http import integration_timeout
from app.jobs.torrent_effects import (
    TorrentEffectHandlers,
    TorrentRetentionReaper,
    TorrentSyncEnqueuer,
)
from app.jobs.torrent_payloads import MAX_MANAGED_TORRENT_BYTES, TorrentPayloadStore
from app.jobs.worker import TorrentWorker, TorrentWorkerConfig
from app.options import PostgresOptionsRegistry
from app.storage import SharedContentStore

logger = logging.getLogger(__name__)
WORKER_DATABASE_RETRY_SECONDS = 5.0


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
            for secret in (spec.qbittorrent_password.get_secret_value(),)
        ):
            raise RuntimeError("v2_worker_integration_secret_invalid")
        return
    if any(value is not None for value in legacy_values):
        raise RuntimeError("v2_worker_integration_registry_required")
    if settings.environment == "production":
        raise RuntimeError("v2_worker_integrations_required")


async def _read_worker_config() -> TorrentWorkerConfig:
    async with session_factory() as session, session.begin():
        registry = PostgresOptionsRegistry()
        await registry.initialize(session)
        runtime_options = await registry.snapshot(session)
    configured_concurrency = runtime_options.get("WOS_WORKER_CONCURRENCY")
    if type(configured_concurrency) is not int or not 1 <= configured_concurrency <= 16:
        raise RuntimeError("worker_concurrency_option_invalid")
    return TorrentWorkerConfig(concurrency=configured_concurrency)


async def _load_worker_config(stop: asyncio.Event) -> TorrentWorkerConfig | None:
    """Wait for SQL at startup, while allowing shutdown to cancel connection work."""
    stopping = asyncio.create_task(stop.wait())
    reading: asyncio.Task[TorrentWorkerConfig] | None = None
    try:
        while not stop.is_set():
            reading = asyncio.create_task(_read_worker_config())
            await asyncio.wait({reading, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if stop.is_set():
                return None
            try:
                return await reading
            except (SQLAlchemyError, OSError):
                logger.warning("torrent_worker_database_unavailable")
            with suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=WORKER_DATABASE_RETRY_SECONDS)
        return None
    finally:
        pending = [task for task in (reading, stopping) if task is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def main() -> None:
    settings = get_settings()
    validate_worker_runtime(settings)
    redis = RedisCoordinator.from_settings(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_number, stop.set)
    try:
        worker_config = await _load_worker_config(stop)
        if worker_config is not None and not stop.is_set():
            await _run_worker(settings, redis, worker_config)
    finally:
        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signal_number)
        await redis.aclose()
        await engine.dispose()


async def _run_worker(
    settings: Settings, redis: RedisCoordinator, worker_config: TorrentWorkerConfig
) -> None:
    if settings.integration_accounts_json is None:
        worker = TorrentWorker(
            session_factory,
            redis,
            {},
            worker_id=_worker_id(),
            config=worker_config,
        )
        retention_reaper = TorrentRetentionReaper(session_factory, redis)
        loop = asyncio.get_running_loop()

        def request_stop() -> None:
            worker.request_stop()
            retention_reaper.request_stop()

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, request_stop)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(worker.run())
            tasks.create_task(retention_reaper.run())
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
            config=worker_config,
        )
        sync_enqueuer = TorrentSyncEnqueuer(session_factory, redis)
        retention_reaper = TorrentRetentionReaper(session_factory, redis)
        loop = asyncio.get_running_loop()

        def request_stop() -> None:
            worker.request_stop()
            sync_enqueuer.request_stop()
            retention_reaper.request_stop()

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, request_stop)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(worker.run())
            tasks.create_task(sync_enqueuer.run())
            tasks.create_task(retention_reaper.run())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
