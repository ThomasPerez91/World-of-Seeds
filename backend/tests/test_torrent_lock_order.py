import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_storage_admission_concurrency import postgres_storage as postgres_storage
from test_v2_torrent_api import RecordingRedis, torrent_content

from app.auth.security import hash_password
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.jobs.torrent_effects import ADD_TORRENT_JOB
from app.jobs.torrent_payloads import TorrentPayloadStore
from app.main import create_app
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    StorageLedger,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    User,
    UserStorageUsage,
)
from app.options import PostgresOptionsRegistry
from app.torrents import cancel_owned_torrent_request, create_or_get_torrent_request
from app.torrents.lifecycle import expire_ready_torrents_batch


async def seed_torrent(
    sessions: async_sessionmaker[AsyncSession],
    prefix: str,
    root: Path,
    *,
    ready: bool,
) -> tuple[User, ManagedTorrent, TorrentRequest]:
    now = datetime.now(UTC)
    user = User(id=uuid.uuid4(), username=prefix, password_hash=hash_password("test-password-long"))
    key = uuid.uuid4()
    parsed = TorrentPayloadStore(root, allowed_tracker_hosts=["c411.org"]).stage(
        torrent_content(name=prefix.encode(), size=600),
        storage_key=key,
    )
    managed = ManagedTorrent(
        id=uuid.uuid4(),
        name=prefix,
        info_hash=parsed.info_hash,
        total_size=600,
        storage_key=key,
        state=ManagedTorrentState.READY if ready else ManagedTorrentState.ERROR,
        progress=1 if ready else 0,
        ready_at=now - timedelta(hours=2) if ready else None,
        retention_expires_at=now + timedelta(days=1) if ready else None,
    )
    request = TorrentRequest(
        id=uuid.uuid4(),
        user_id=user.id,
        managed_torrent_id=managed.id,
        state=TorrentRequestState.READY if ready else TorrentRequestState.ACTIVE,
        ready_at=now - timedelta(hours=2) if ready else None,
        unsubscribe_at=now - timedelta(hours=1) if ready else None,
    )
    async with sessions() as session, session.begin():
        await PostgresOptionsRegistry().initialize(session)
        session.add(user)
        await session.flush()
        session.add(managed)
        await session.flush()
        session.add_all([request, UserStorageUsage(user_id=user.id, logical_bytes=600)])
        await session.flush()
        session.add(
            TorrentJob(
                managed_torrent_id=managed.id,
                torrent_request_id=request.id,
                job_type=ADD_TORRENT_JOB,
                idempotency_key=f"lock-test:{managed.id}",
                state=TorrentJobState.COMPLETED if ready else TorrentJobState.FAILED,
                available_at=now,
                finished_at=now,
            )
        )
        ledger = await session.get(StorageLedger, 1, with_for_update=True)
        assert ledger is not None
        ledger.managed_bytes += 600
    return user, managed, request


@pytest.mark.asyncio
async def test_retry_waiting_for_a_torrent_does_not_block_its_cancellation(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, prefix = postgres_storage
    user, torrent, request = await seed_torrent(sessions, prefix, tmp_path, ready=False)
    settings = Settings(data_root=tmp_path, static_root=tmp_path / "absent")
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    redis = RecordingRedis()
    application.state.redis_coordinator = redis

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    application.dependency_overrides[get_db_session] = request_session
    original_scalar = AsyncSession.scalar
    waiting_for_torrent = asyncio.Event()
    retry_task = None

    async def scalar(session: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            asyncio.current_task() is retry_task
            and "FROM managed_torrents" in str(statement)
            and getattr(statement, "_for_update_arg", None) is not None
        ):
            waiting_for_torrent.set()
        return await original_scalar(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={
                    "username": user.username,
                    "password": "test-password-long",
                },
            )
            assert login.status_code == 200
            token = client.cookies.get("wos_csrf")
            assert token is not None
            async with sessions() as blocker, blocker.begin():
                assert (
                    await blocker.get(ManagedTorrent, torrent.id, with_for_update=True) is not None
                )
                retry_task = asyncio.create_task(
                    client.post(
                        f"/api/v2/torrents/{request.id}/retry",
                        headers={"X-CSRF-Token": token},
                    )
                )
                async with asyncio.timeout(2):
                    await waiting_for_torrent.wait()
                # With right->torrent ordering retry owns this right while waiting
                # for our torrent, and this cancellation completes the deadlock cycle.
                async with asyncio.timeout(3):
                    cancelled = await cancel_owned_torrent_request(
                        blocker,
                        user_id=user.id,
                        torrent_request_id=request.id,
                        retention_hours=48,
                    )
                assert cancelled is not None and cancelled.cancelled
            response = await retry_task
            assert response.status_code == 404
            assert response.json()["detail"]["code"] == "torrent_request_not_found"
            assert redis.signals == 0 and redis.events == []
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(TorrentRequest.state).where(
                        TorrentRequest.id == request.id,
                    )
                )
                is TorrentRequestState.CANCELLED
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TorrentJob)
                    .where(
                        TorrentJob.managed_torrent_id == torrent.id,
                        TorrentJob.job_type == ADD_TORRENT_JOB,
                        TorrentJob.state == TorrentJobState.QUEUED,
                    )
                )
                == 0
            )
    finally:
        if retry_task is not None:
            if not retry_task.done():
                retry_task.cancel()
            await asyncio.gather(retry_task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cancel", "upload", "release"])
async def test_expiration_skips_a_busy_torrent_without_locking_its_rights(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    tmp_path: Path,
    operation: str,
) -> None:
    sessions, prefix = postgres_storage
    user, torrent, request = await seed_torrent(sessions, prefix, tmp_path, ready=True)
    now = datetime.now(UTC)
    async with sessions() as blocker, blocker.begin():
        assert await blocker.get(ManagedTorrent, torrent.id, with_for_update=True) is not None
        async with sessions() as maintenance, maintenance.begin():
            async with asyncio.timeout(1):
                skipped = await expire_ready_torrents_batch(maintenance, now=now)
            assert all(item.managed_torrent_id != torrent.id for item in skipped)
        if operation == "cancel":
            async with asyncio.timeout(2):
                result = await cancel_owned_torrent_request(
                    blocker,
                    user_id=user.id,
                    torrent_request_id=request.id,
                    retention_hours=48,
                )
            assert result is not None and result.cancelled
        elif operation == "upload":
            async with asyncio.timeout(2):
                shared = await create_or_get_torrent_request(
                    blocker,
                    user_id=user.id,
                    info_hash=torrent.info_hash,
                    name=torrent.name,
                    total_size=torrent.total_size,
                    now=now,
                )
            assert shared.request_created
            assert shared.request.state is TorrentRequestState.READY
    async with sessions() as maintenance, maintenance.begin():
        expired = await expire_ready_torrents_batch(maintenance, now=now)
        own_results = [item for item in expired if item.managed_torrent_id == torrent.id]
        assert len(own_results) == (1 if operation == "release" else 0)
    async with sessions() as session:
        state = await session.scalar(
            select(TorrentRequest.state).where(TorrentRequest.id == request.id)
        )
        assert state is (
            TorrentRequestState.CANCELLED if operation == "cancel" else TorrentRequestState.EXPIRED
        )
        usage = await session.get(UserStorageUsage, user.id)
        assert usage is not None and usage.logical_bytes == (600 if operation == "upload" else 0)
    async with sessions() as maintenance, maintenance.begin():
        assert all(
            item.managed_torrent_id != torrent.id
            for item in (await expire_ready_torrents_batch(maintenance, now=now))
        )


@pytest.mark.asyncio
async def test_cancellation_reloads_a_right_changed_while_waiting_for_its_torrent(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, prefix = postgres_storage
    user, torrent, request = await seed_torrent(sessions, prefix, tmp_path, ready=True)
    original_get = AsyncSession.get
    waiting_for_torrent = asyncio.Event()
    cancellation_task = None

    async def get(session: AsyncSession, entity: Any, *args: Any, **kwargs: Any) -> Any:
        if asyncio.current_task() is cancellation_task and entity is ManagedTorrent:
            waiting_for_torrent.set()
        return await original_get(session, entity, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", get)

    async def cancel() -> Any:
        async with sessions() as session, session.begin():
            return await cancel_owned_torrent_request(
                session,
                user_id=user.id,
                torrent_request_id=request.id,
                retention_hours=48,
            )

    try:
        async with sessions() as blocker, blocker.begin():
            assert await blocker.get(ManagedTorrent, torrent.id, with_for_update=True) is not None
            cancellation_task = asyncio.create_task(cancel())
            async with asyncio.timeout(2):
                await waiting_for_torrent.wait()
            right = await blocker.get(TorrentRequest, request.id, with_for_update=True)
            usage = await blocker.get(UserStorageUsage, user.id, with_for_update=True)
            assert right is not None and usage is not None
            right.state = TorrentRequestState.EXPIRED
            right.expires_at = datetime.now(UTC)
            usage.logical_bytes = 0
        assert await cancellation_task is None
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(TorrentRequest.state).where(
                        TorrentRequest.id == request.id,
                    )
                )
                is TorrentRequestState.EXPIRED
            )
    finally:
        if cancellation_task is not None:
            if not cancellation_task.done():
                cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_busy_shared_torrent_does_not_fill_the_expiration_page(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    tmp_path: Path,
) -> None:
    sessions, prefix = postgres_storage
    busy_owner, busy, busy_right = await seed_torrent(
        sessions, prefix + "-busy", tmp_path, ready=True
    )
    free_owner, free, free_right = await seed_torrent(
        sessions, prefix + "-free", tmp_path, ready=True
    )
    other_owner = User(id=uuid.uuid4(), username=prefix + "-other", password_hash="hash")
    async with sessions() as session, session.begin():
        session.add(other_owner)
        await session.flush()
        session.add_all(
            [
                TorrentRequest(
                    user_id=other_owner.id,
                    managed_torrent_id=busy.id,
                    state=TorrentRequestState.READY,
                    ready_at=busy_right.ready_at,
                    unsubscribe_at=busy_right.unsubscribe_at,
                ),
                UserStorageUsage(user_id=other_owner.id, logical_bytes=600),
            ]
        )
    now = datetime.now(UTC)
    async with sessions() as blocker, blocker.begin():
        assert await blocker.get(ManagedTorrent, busy.id, with_for_update=True) is not None
        async with sessions() as maintenance, maintenance.begin():
            async with asyncio.timeout(2):
                results = await expire_ready_torrents_batch(maintenance, now=now, limit=2)
            assert sum(len(item.requests) for item in results) <= 2
            assert any(item.managed_torrent_id == free.id for item in results)
            assert all(item.managed_torrent_id != busy.id for item in results)
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(TorrentRequest.state).where(
                        TorrentRequest.id == free_right.id,
                    )
                )
                is TorrentRequestState.EXPIRED
            )
            assert (
                await session.scalar(
                    select(TorrentRequest.state).where(
                        TorrentRequest.id == busy_right.id,
                    )
                )
                is TorrentRequestState.READY
            )
            usage = await session.get(UserStorageUsage, free_owner.id)
            assert usage is not None and usage.logical_bytes == 0
    async with sessions() as maintenance, maintenance.begin():
        results = await expire_ready_torrents_batch(maintenance, now=now, limit=2)
        assert any(
            item.managed_torrent_id == busy.id and len(item.requests) == 2 for item in results
        )
    async with sessions() as session:
        for user_id in (busy_owner.id, other_owner.id):
            usage = await session.get(UserStorageUsage, user_id)
            assert usage is not None and usage.logical_bytes == 0
