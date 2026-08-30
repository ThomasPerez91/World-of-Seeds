from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import socket
import uuid
from datetime import timedelta

import httpx

from app.coordination import RedisCoordinator
from app.core.config import get_settings
from app.core.database import engine, session_factory
from app.integrations.account_routing import (
    build_deployment_account_router,
    parse_deployment_account_specs,
)
from app.integrations.observability_v2 import V2IntegrationObservabilityPublisher
from app.integrations.qbittorrent_v2 import QBittorrentV2Gateway
from app.jobs.torrent_payloads import MAX_MANAGED_TORRENT_BYTES
from app.scheduler.runtime import ManagedControlGateway, SchedulerRuntime


def _scheduler_id() -> str:
    hostname = re.sub(r"[^A-Za-z0-9_.-]", "-", socket.gethostname())[:48] or "host"
    return f"scheduler:{hostname}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


async def main() -> None:
    settings = get_settings()
    if settings.integration_accounts_json is not None and any(
        value is not None
        for value in (
            settings.qbittorrent_url,
            settings.qbittorrent_username,
            settings.qbittorrent_password,
        )
    ):
        raise RuntimeError("V2 scheduler integration configuration is ambiguous")
    if settings.integration_accounts_json is None and (
        settings.qbittorrent_url is None
        or settings.qbittorrent_username is None
        or settings.qbittorrent_password is None
    ):
        raise RuntimeError("V2 scheduler qBittorrent configuration is incomplete")
    redis = RedisCoordinator.from_settings(settings)
    timeout = httpx.Timeout(
        connect=settings.integration_connect_timeout_seconds,
        read=settings.integration_read_timeout_seconds,
        write=settings.integration_read_timeout_seconds,
        pool=settings.integration_connect_timeout_seconds,
    )
    async with (
        httpx.AsyncClient(timeout=timeout) as client,
        httpx.AsyncClient(timeout=timeout) as observability_client,
    ):
        observability: V2IntegrationObservabilityPublisher | None = None
        if settings.integration_accounts_json is not None:
            gateway: ManagedControlGateway = build_deployment_account_router(
                settings.integration_accounts_json,
                client,
                session_factory,
                allowed_tracker_hosts=settings.c411_tracker_hosts,
                data_root=settings.qbittorrent_data_root,
                max_total_size=MAX_MANAGED_TORRENT_BYTES,
            )
            observability = V2IntegrationObservabilityPublisher(
                session_factory,
                observability_client,
                parse_deployment_account_specs(settings.integration_accounts_json),
                data_root=settings.qbittorrent_data_root,
                interval=timedelta(seconds=settings.integration_health_cache_seconds),
            )
        else:
            assert settings.qbittorrent_url is not None
            assert settings.qbittorrent_username is not None
            assert settings.qbittorrent_password is not None
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
            redis=redis,
        )
        loop = asyncio.get_running_loop()

        def request_stop() -> None:
            scheduler.request_stop()
            if observability is not None:
                observability.request_stop()

        for signal_number in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_number, request_stop)
        try:
            if observability is None:
                await scheduler.run()
            else:
                async with asyncio.TaskGroup() as tasks:
                    tasks.create_task(scheduler.run())
                    tasks.create_task(observability.run())
        finally:
            await redis.aclose()
            await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
