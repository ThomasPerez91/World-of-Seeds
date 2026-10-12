import asyncio
import os
import threading
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.admin import ReconciliationCursor
from app.auth.security import hash_password
from app.core.config import Settings
from app.core.database import get_db_session
from app.main import create_app
from app.models import Base, ManagedTorrent, ManagedTorrentState, QBittorrentInventorySnapshot, User
from app.storage.shared import SharedContentInventory, SharedContentStore, SharedContentStoreError


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("phase", ["database", "storage"])
@pytest.mark.parametrize("outcome", ["success", "storage_error", "cancel", "concurrent_update"])
async def test_reconciliation_disk_wait_does_not_hold_the_only_sql_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    phase: Literal["database", "storage"],
    outcome: str,
) -> None:
    if backend == "postgres" and not os.environ.get("WOS_DATABASE_URL", "").startswith(
        "postgresql"
    ):
        pytest.skip("Requires isolated PostgreSQL CI service")
    url = (
        os.environ["WOS_DATABASE_URL"]
        if backend == "postgres"
        else f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}"
    )
    engine = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=0.05)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    admin = User(
        id=uuid.uuid4(),
        username=f"pool-{uuid.uuid4().hex[:16]}",
        password_hash=hash_password("test-password-long"),
        is_admin=True,
    )
    torrent = ManagedTorrent(
        id=uuid.uuid4(),
        info_hash=uuid.uuid4().hex + uuid.uuid4().hex[:8],
        name="pool-test",
        total_size=1,
        state=ManagedTorrentState.READY,
        storage_key=uuid.uuid4(),
    )
    snapshot = QBittorrentInventorySnapshot(
        id=uuid.uuid4(),
        account_ref=uuid.uuid4(),
        observation_set=uuid.uuid4(),
        item_count=0,
        truncated=False,
        checked_at=datetime.now(UTC),
    )
    async with sessions() as session, session.begin():
        session.add_all([admin, torrent, snapshot])
    application = create_app(Settings(data_root=tmp_path, static_root=tmp_path / "absent"))

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    application.dependency_overrides[get_db_session] = request_session
    entered = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()
    loop = asyncio.get_running_loop()

    def disk_wait() -> None:
        loop.call_soon_threadsafe(entered.set)
        try:
            if not release.wait(5):
                raise AssertionError("Disk test was not released")
            if outcome == "storage_error":
                raise SharedContentStoreError("PRIVATE disk failure")
        finally:
            finished.set()

    def contains(_store: SharedContentStore, key: uuid.UUID) -> bool:
        disk_wait()
        return False

    def inventory(
        _store: SharedContentStore, *, limit: int, after: uuid.UUID | None
    ) -> SharedContentInventory:
        disk_wait()
        return SharedContentInventory((torrent.storage_key,), 0, False)

    monkeypatch.setattr(SharedContentStore, "contains", contains)
    monkeypatch.setattr(SharedContentStore, "inventory_page", inventory)
    cursor = ReconciliationCursor(phase, snapshot_ids=(snapshot.id,)).encode()
    task = None
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"username": admin.username, "password": "test-password-long"},
            )
            assert login.status_code == 200
            task = asyncio.create_task(
                client.get("/api/v2/admin/reconciliation", params={"cursor": cursor, "limit": 200})
            )
            async with asyncio.timeout(2):
                await entered.wait()
            # This is a genuine second checkout while disk I/O is deliberately paused.
            # With the old code it fails because the request keeps the sole connection.
            async with engine.begin() as independent:
                assert await independent.scalar(text("SELECT 1")) == 1
                if outcome == "concurrent_update":
                    await independent.execute(
                        update(ManagedTorrent)
                        .where(ManagedTorrent.id == torrent.id)
                        .values(state=ManagedTorrentState.PURGED)
                    )
            if outcome == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                release.set()
            else:
                release.set()
                response = await task
                if phase == "storage" and outcome == "storage_error":
                    assert response.status_code == 503
                else:
                    assert response.status_code == 200
                    body = response.json()
                    codes = {item["code"] for item in body["anomalies"]}
                    if phase == "database":
                        assert "missing_qb_torrent" in codes
                        assert (
                            "storage_unavailable"
                            if outcome == "storage_error"
                            else "missing_storage"
                        ) in codes
                    else:
                        assert body["storage_scanned"] == 1
                        assert "orphan_storage" not in codes
                    if phase == "database":
                        assert body["next_cursor"] is not None
            async with engine.connect() as recovered:
                assert await recovered.scalar(text("SELECT 1")) == 1
    finally:
        release.set()
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if entered.is_set():
            assert await asyncio.to_thread(finished.wait, 2)
        async with sessions() as cleanup, cleanup.begin():
            await cleanup.execute(delete(ManagedTorrent).where(ManagedTorrent.id == torrent.id))
            await cleanup.execute(
                delete(QBittorrentInventorySnapshot).where(
                    QBittorrentInventorySnapshot.id == snapshot.id
                )
            )
            await cleanup.execute(delete(User).where(User.id == admin.id))
        await engine.dispose()
