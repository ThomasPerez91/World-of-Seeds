import asyncio
import logging
from datetime import timedelta
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.integrations.account_routing import DeploymentAccountSpec
from app.integrations.observability_v2 import (
    V2IntegrationObservabilityPublisher,
    load_v2_external_services_snapshot,
)
from app.models import Base, IntegrationServiceHealth, QBittorrentInventorySnapshot


def _spec() -> DeploymentAccountSpec:
    return DeploymentAccountSpec(
        qbittorrent_account_ref=uuid4(),
        newgreedy_url="http://newgreedy:3456",
        qbittorrent_url="http://qbittorrent:8080",
        qbittorrent_username="FAKE_USER",
        qbittorrent_password=SecretStr("FAKE_TEST_PASSWORD"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["account_write", "cycle_finalize"])
async def test_database_failure_retries_without_cancelling_scheduler_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_stage: str,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'observability.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    failed = False
    attempts: list[float] = []
    finished = asyncio.Event()
    sibling_completed = False
    sibling_cancelled = False

    def fail_statement(
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        nonlocal failed
        is_target = (
            statement.startswith("INSERT INTO integration_service_health")
            if failure_stage == "account_write"
            else statement.startswith("UPDATE integration_service_health SET valid_until")
        )
        if not failed and is_target:
            failed = True
            raise OperationalError(statement, parameters, RuntimeError("FAKE_PRIVATE_DB_DETAIL"))

    event.listen(engine.sync_engine, "before_cursor_execute", fail_statement)

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"total": 0})
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.")
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(204)
        assert request.url.path == "/api/v2/torrents/info"
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        publisher = V2IntegrationObservabilityPublisher(
            sessions,
            client,
            [_spec()],
            data_root=tmp_path,
            interval=timedelta(seconds=0.03),
        )
        original = publisher.refresh_once

        async def refresh() -> None:
            attempts.append(monotonic())
            await original()
            publisher.request_stop()
            finished.set()

        async def scheduler_sibling() -> None:
            nonlocal sibling_completed, sibling_cancelled
            try:
                await finished.wait()
                sibling_completed = True
            except asyncio.CancelledError:
                sibling_cancelled = True
                raise

        monkeypatch.setattr(publisher, "refresh_once", refresh)
        caplog.set_level(logging.WARNING)
        try:
            async with asyncio.timeout(3), asyncio.TaskGroup() as tasks:
                tasks.create_task(publisher.run())
                tasks.create_task(scheduler_sibling())
            assert failed and len(attempts) == 2
            assert attempts[1] - attempts[0] >= 0.03
            assert sibling_completed and not sibling_cancelled
            async with sessions() as check:
                snapshot = await load_v2_external_services_snapshot(check)
                assert snapshot.healthy
                assert (
                    await check.scalar(select(func.count()).select_from(IntegrationServiceHealth))
                    == 2
                )
                assert (
                    await check.scalar(
                        select(func.count()).select_from(QBittorrentInventorySnapshot)
                    )
                    or 0
                ) >= 1
            assert "integration_observability_database_unavailable" in caplog.text
            assert "FAKE_PRIVATE_DB_DETAIL" not in caplog.text
            assert "FAKE_TEST_PASSWORD" not in caplog.text
        finally:
            publisher.request_stop()
            event.remove(engine.sync_engine, "before_cursor_execute", fail_statement)
            await engine.dispose()


@pytest.mark.asyncio
async def test_observability_cancellation_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    entered = asyncio.Event()
    async with httpx.AsyncClient(trust_env=False) as client:
        publisher = V2IntegrationObservabilityPublisher(
            async_sessionmaker(engine),
            client,
            [_spec()],
            data_root=tmp_path,
        )

        async def refresh() -> None:
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(publisher, "refresh_once", refresh)
        task = asyncio.create_task(publisher.run())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert "integration_observability_database_unavailable" not in caplog.text
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await engine.dispose()


@pytest.mark.asyncio
async def test_observability_unexpected_error_remains_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with httpx.AsyncClient(trust_env=False) as client:
        publisher = V2IntegrationObservabilityPublisher(
            async_sessionmaker(engine),
            client,
            [_spec()],
            data_root=tmp_path,
        )

        async def refresh() -> None:
            raise RuntimeError("unexpected_test_failure")

        monkeypatch.setattr(publisher, "refresh_once", refresh)
        try:
            with pytest.raises(RuntimeError, match="unexpected_test_failure"):
                await publisher.run()
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_stop_interrupts_database_retry_wait_without_another_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    failed = asyncio.Event()
    attempts = 0
    async with httpx.AsyncClient(trust_env=False) as client:
        publisher = V2IntegrationObservabilityPublisher(
            async_sessionmaker(engine),
            client,
            [_spec()],
            data_root=tmp_path,
            interval=timedelta(minutes=5),
        )

        async def refresh() -> None:
            nonlocal attempts
            attempts += 1
            failed.set()
            raise OperationalError("FAKE_SQL", {}, RuntimeError("FAKE_PRIVATE_DB_DETAIL"))

        monkeypatch.setattr(publisher, "refresh_once", refresh)
        task = asyncio.create_task(publisher.run())
        try:
            await asyncio.wait_for(failed.wait(), 1)
            assert not task.done()
            publisher.request_stop()
            await asyncio.wait_for(task, 1)
            assert attempts == 1
            assert "integration_observability_database_unavailable" in caplog.text
            assert "FAKE_PRIVATE_DB_DETAIL" not in caplog.text
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await engine.dispose()
