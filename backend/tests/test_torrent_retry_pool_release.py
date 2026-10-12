import asyncio
import os
import threading
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_v2_torrent_api import RecordingRedis, torrent_content

from app.auth.security import hash_password
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.jobs.torrent_effects import ADD_TORRENT_JOB
from app.jobs.torrent_payloads import TorrentPayloadStore, TorrentPayloadStoreError
from app.main import create_app
from app.models import (
    Base,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.options import PostgresOptionsRegistry
from app.torrents import ParsedTorrent


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "outcome",
    ["success", "storage_error", "cancel", "unsubscribe", "generation", "storage_key", "metadata"],
)
async def test_retry_releases_sql_during_disk_read_and_revalidates_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    outcome: str,
) -> None:
    if backend == "postgres" and not os.environ.get("WOS_DATABASE_URL", "").startswith(
        "postgresql"
    ):
        pytest.skip("Requires isolated PostgreSQL CI service")
    url = (
        os.environ["WOS_DATABASE_URL"]
        if backend == "postgres"
        else f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}"
    )
    engine = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=0.05)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    user = User(
        id=uuid.uuid4(),
        username=f"retry-{uuid.uuid4().hex[:16]}",
        password_hash=hash_password("test-password-long"),
    )
    store = TorrentPayloadStore(tmp_path, allowed_tracker_hosts=["c411.org"])
    key = uuid.uuid4()
    parsed = store.stage(torrent_content(), storage_key=key)
    managed = ManagedTorrent(
        id=uuid.uuid4(),
        info_hash=parsed.info_hash,
        name=parsed.name,
        total_size=parsed.total_size,
        state=ManagedTorrentState.ERROR,
        storage_key=key,
    )
    request = TorrentRequest(
        id=uuid.uuid4(),
        user_id=user.id,
        managed_torrent_id=managed.id,
        state=TorrentRequestState.ACTIVE,
    )
    failed = TorrentJob(
        managed_torrent_id=managed.id,
        torrent_request_id=request.id,
        job_type=ADD_TORRENT_JOB,
        idempotency_key=f"retry-test:{managed.id}",
        state=TorrentJobState.FAILED,
        available_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    async with sessions() as session, session.begin():
        await PostgresOptionsRegistry().initialize(session)
        session.add(user)
        await session.flush()
        session.add(managed)
        await session.flush()
        session.add_all([request, failed])
    settings = Settings(data_root=tmp_path, static_root=tmp_path / "absent")
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    redis = RecordingRedis()
    application.state.redis_coordinator = redis

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    application.dependency_overrides[get_db_session] = request_session
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    loop = asyncio.get_running_loop()
    original_read = TorrentPayloadStore.read

    def read_payload(payload_store: TorrentPayloadStore, storage_key: uuid.UUID) -> ParsedTorrent:
        loop.call_soon_threadsafe(entered.set)
        try:
            if not release.wait(5):
                raise AssertionError("Disk test was not released")
            if outcome == "storage_error":
                raise TorrentPayloadStoreError("PRIVATE disk failure")
            return original_read(payload_store, storage_key)
        finally:
            finished.set()

    monkeypatch.setattr(TorrentPayloadStore, "read", read_payload)
    task = None
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"username": user.username, "password": "test-password-long"},
            )
            assert login.status_code == 200
            token = client.cookies.get("wos_csrf")
            assert token is not None
            task = asyncio.create_task(
                client.post(
                    f"/api/v2/torrents/{request.id}/retry",
                    headers={"X-CSRF-Token": token},
                )
            )
            async with asyncio.timeout(2):
                await entered.wait()
            # A genuine independent checkout and commit while the disk thread is paused.
            async with engine.begin() as independent:
                assert await independent.scalar(text("SELECT 1")) == 1
                if outcome == "unsubscribe":
                    await independent.execute(
                        update(TorrentRequest)
                        .where(TorrentRequest.id == request.id)
                        .values(state=TorrentRequestState.CANCELLED)
                    )
                elif outcome == "generation":
                    await independent.execute(
                        update(ManagedTorrent)
                        .where(ManagedTorrent.id == managed.id)
                        .values(lifecycle_generation=ManagedTorrent.lifecycle_generation + 1)
                    )
                elif outcome == "storage_key":
                    await independent.execute(
                        update(ManagedTorrent)
                        .where(ManagedTorrent.id == managed.id)
                        .values(storage_key=uuid.uuid4())
                    )
                elif outcome == "metadata":
                    await independent.execute(
                        update(ManagedTorrent)
                        .where(ManagedTorrent.id == managed.id)
                        .values(name="different")
                    )
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                release.set()
            else:
                release.set()
                response = await task
                expected = {
                    "success": (200, None),
                    "storage_error": (409, "torrent_payload_unavailable"),
                    "unsubscribe": (404, "torrent_request_not_found"),
                    "generation": (409, "torrent_retry_not_allowed"),
                    "storage_key": (409, "torrent_retry_not_allowed"),
                    "metadata": (409, "torrent_payload_mismatch"),
                }
                code, detail = expected[outcome]
                assert response.status_code == code, response.text
                if detail is not None:
                    assert response.json()["detail"]["code"] == detail
                assert "PRIVATE" not in response.text
            async with sessions() as verified:
                queued = await verified.scalar(
                    select(func.count())
                    .select_from(TorrentJob)
                    .where(
                        TorrentJob.managed_torrent_id == managed.id,
                        TorrentJob.state == TorrentJobState.QUEUED,
                    )
                )
                state = await verified.scalar(
                    select(ManagedTorrent.state).where(ManagedTorrent.id == managed.id)
                )
            assert queued == (1 if outcome == "success" else 0)
            assert state is (
                ManagedTorrentState.PENDING if outcome == "success" else ManagedTorrentState.ERROR
            )
            assert redis.signals == (1 if outcome == "success" else 0)
            assert len(redis.events) == (1 if outcome == "success" else 0)
    finally:
        release.set()
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if entered.is_set():
            assert await asyncio.to_thread(finished.wait, 2)
        async with sessions() as cleanup, cleanup.begin():
            await cleanup.execute(
                delete(TorrentJob).where(TorrentJob.managed_torrent_id == managed.id)
            )
            await cleanup.execute(delete(TorrentRequest).where(TorrentRequest.id == request.id))
            await cleanup.execute(delete(ManagedTorrent).where(ManagedTorrent.id == managed.id))
            await cleanup.execute(delete(User).where(User.id == user.id))
        await engine.dispose()
