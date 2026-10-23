import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_v2_torrent_api import RecordingRedis, torrent_content

from app.api.routes import torrents_v2
from app.auth.security import hash_password
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.jobs.torrent_effects import ADD_TORRENT_JOB
from app.jobs.torrent_payloads import TorrentPayloadStore
from app.main import create_app
from app.models import (
    Base,
    ManagedTorrent,
    ManagedTorrentState,
    StorageLedger,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    User,
)
from app.options import PostgresOptionsRegistry
from app.storage import SharedContentStore, StorageAdmissionPolicy, StorageDiskSnapshot, accounting
from app.torrents import create_or_get_torrent_request
from app.torrents.validation_work import TorrentValidationPool


@pytest_asyncio.fixture
async def postgres_storage() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], str]]:
    url = os.environ.get("WOS_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("Requires isolated PostgreSQL CI service")
    engine = create_async_engine(
        url,
        pool_size=12,
        max_overflow=0,
        pool_timeout=1,
        connect_args={"command_timeout": 3},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    prefix = f"admission-{uuid.uuid4().hex[:12]}"
    async with sessions() as session:
        ledger = await session.get(StorageLedger, 1)
        assert ledger is not None
        original = {
            key: getattr(ledger, key)
            for key in (
                "managed_bytes",
                "disk_total_bytes",
                "disk_free_bytes",
                "pressure",
                "updated_at",
            )
        }
    try:
        yield sessions, prefix
    finally:
        async with sessions() as session, session.begin():
            await session.execute(
                delete(ManagedTorrent).where(ManagedTorrent.name.startswith(prefix))
            )
            await session.execute(delete(User).where(User.username.startswith(prefix)))
            ledger = await session.get(StorageLedger, 1, with_for_update=True)
            assert ledger is not None
            for key, value in original.items():
                setattr(ledger, key, value)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["upload", "purge"])
async def test_reconciliation_aggregates_after_a_concurrent_accounting_commit(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    sessions, prefix = postgres_storage
    now = datetime.now(UTC)
    disk = StorageDiskSnapshot(10**12, 10**12)
    policy = StorageAdmissionPolicy(0, 0, 0, 0, 80, 90)
    user = User(id=uuid.uuid4(), username=prefix, password_hash="hash")
    async with sessions() as session, session.begin():
        session.add(user)
    managed_id = None
    if mutation == "purge":
        async with sessions() as session, session.begin():
            result = await create_or_get_torrent_request(
                session,
                user_id=user.id,
                info_hash=uuid.uuid4().hex + "00000000",
                name=prefix,
                total_size=600,
                storage_policy=policy,
                disk_snapshot=disk,
            )
            managed_id = result.managed_torrent.id
    original_lock = accounting.lock_storage_ledger
    reconciliation_at_lock = asyncio.Event()
    reconcile_task = None

    async def ledger_lock(session: AsyncSession, *, now: datetime) -> StorageLedger:
        if asyncio.current_task() is reconcile_task:
            reconciliation_at_lock.set()
        return await original_lock(session, now=now)

    monkeypatch.setattr(accounting, "lock_storage_ledger", ledger_lock)

    async def reconcile() -> int:
        async with sessions() as session, session.begin():
            result = await accounting.reconcile_storage_counters(
                session,
                policy=policy,
                disk=disk,
                # No user locks: isolate the shared ledger/aggregate interleaving.
                after_user_id=uuid.UUID(int=2**128 - 1),
                now=now,
            )
            return result.managed_bytes

    try:
        async with sessions() as writer, writer.begin():
            if mutation == "upload":
                await create_or_get_torrent_request(
                    writer,
                    user_id=user.id,
                    info_hash=uuid.uuid4().hex + "00000000",
                    name=prefix,
                    total_size=600,
                    storage_policy=policy,
                    disk_snapshot=disk,
                )
            else:
                managed = await writer.get(ManagedTorrent, managed_id, with_for_update=True)
                assert managed is not None
                ledger = await original_lock(writer, now=now)
                ledger.managed_bytes -= managed.total_size
                managed.state = ManagedTorrentState.PURGED
                await writer.flush()
            # Writer owns the ledger but has not committed its reservation change.
            reconcile_task = asyncio.create_task(reconcile())
            async with asyncio.timeout(2):
                await reconciliation_at_lock.wait()
            assert not reconcile_task.done()
        # Commit the writer before releasing reconciliation's SELECT FOR UPDATE.
        reconciled = await reconcile_task
        async with sessions() as session:
            authoritative = await session.scalar(
                select(
                    func.coalesce(
                        func.sum(ManagedTorrent.total_size),
                        0,
                    )
                ).where(ManagedTorrent.state != ManagedTorrentState.PURGED)
            )
            verified_ledger = await session.get(StorageLedger, 1)
            assert verified_ledger is not None
            assert verified_ledger.managed_bytes == reconciled == authoritative
    finally:
        if reconcile_task is not None:
            if not reconcile_task.done():
                reconcile_task.cancel()
            await asyncio.gather(reconcile_task, return_exceptions=True)


@pytest_asyncio.fixture
async def retry_validation_capacity(
    count: int, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    # This test exercises serialized storage admission, after payload validation.
    # Admit all contenders here; production pool saturation has dedicated tests.
    pool = TorrentValidationPool(workers=2, max_pending=count)
    monkeypatch.setattr(torrents_v2, "torrent_validation_pool", pool)
    try:
        yield
    finally:
        await pool.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [2, 8])
async def test_concurrent_retries_share_one_capacity_admission(
    retry_validation_capacity: None,
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> None:
    sessions, prefix = postgres_storage
    now = datetime.now(UTC)
    user = User(id=uuid.uuid4(), username=prefix, password_hash=hash_password("test-password-long"))
    store = TorrentPayloadStore(tmp_path, allowed_tracker_hosts=["c411.org"])
    requests = []
    async with sessions() as session, session.begin():
        await PostgresOptionsRegistry().initialize(session)
        values = await PostgresOptionsRegistry().snapshot(session)
        baseline = int(
            await session.scalar(
                select(
                    func.coalesce(
                        func.sum(ManagedTorrent.total_size),
                        0,
                    )
                ).where(
                    ManagedTorrent.state.in_(
                        (
                            ManagedTorrentState.PENDING,
                            ManagedTorrentState.ADDING,
                            ManagedTorrentState.DOWNLOADING,
                            ManagedTorrentState.PAUSED,
                            ManagedTorrentState.RETRY_WAIT,
                            ManagedTorrentState.READY,
                        )
                    )
                )
            )
            or 0
        )
        session.add(user)
        await session.flush()
        for index in range(count):
            key = uuid.uuid4()
            parsed = store.stage(
                torrent_content(name=f"{prefix}-{index}".encode(), size=600), storage_key=key
            )
            managed = ManagedTorrent(
                id=uuid.uuid4(),
                name=parsed.name,
                info_hash=parsed.info_hash,
                total_size=600,
                state=ManagedTorrentState.ERROR,
                storage_key=key,
            )
            session.add(managed)
            await session.flush()
            request = TorrentRequest(
                id=uuid.uuid4(), user_id=user.id, managed_torrent_id=managed.id
            )
            session.add(request)
            await session.flush()
            session.add(
                TorrentJob(
                    managed_torrent_id=managed.id,
                    torrent_request_id=request.id,
                    job_type=ADD_TORRENT_JOB,
                    idempotency_key=f"concurrent:{managed.id}",
                    state=TorrentJobState.FAILED,
                    available_at=now,
                    finished_at=now,
                )
            )
            requests.append(request.id)
    monkeypatch.setattr(SharedContentStore, "disk_capacity", lambda _self: (10**12, 10**12))
    values["WOS_STORAGE_MANAGED_MAX_BYTES"] = baseline + 1_000
    settings = Settings(data_root=tmp_path, static_root=tmp_path / "absent")
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    redis = RecordingRedis()
    application.state.redis_coordinator = redis
    original_scalar = AsyncSession.scalar

    async def slow_capacity_read(
        session: AsyncSession, statement: Any, *args: Any, **kwargs: Any
    ) -> Any:
        result = await original_scalar(session, statement, *args, **kwargs)
        if "sum(managed_torrents.total_size)" in str(statement):
            # Real result, deliberately delayed before the route applies its decision.
            # Without shared admission several requests can all observe the old total.
            await asyncio.sleep(0.1)
        return result

    monkeypatch.setattr(AsyncSession, "scalar", slow_capacity_read)

    async def option_snapshot(_registry: PostgresOptionsRegistry, _session: AsyncSession) -> Any:
        return values.copy()

    monkeypatch.setattr(PostgresOptionsRegistry, "snapshot", option_snapshot)

    async def request_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    application.dependency_overrides[get_db_session] = request_session
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
        async with asyncio.timeout(6):
            responses = await asyncio.gather(
                *(
                    client.post(
                        f"/api/v2/torrents/{request_id}/retry",
                        headers={"X-CSRF-Token": token},
                    )
                    for request_id in requests
                )
            )
        assert sorted(response.status_code for response in responses) == [200] + [507] * (count - 1)
        assert all(
            response.json()["detail"]["code"] == "managed_capacity_exceeded"
            for response in responses
            if response.status_code == 507
        )
        assert redis.signals == 1
        assert len(redis.events) == 1
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ManagedTorrent)
                .where(
                    ManagedTorrent.name.startswith(prefix),
                    ManagedTorrent.state == ManagedTorrentState.PENDING,
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(TorrentJob)
                .join(
                    ManagedTorrent,
                    ManagedTorrent.id == TorrentJob.managed_torrent_id,
                )
                .where(
                    ManagedTorrent.name.startswith(prefix),
                    TorrentJob.state == TorrentJobState.QUEUED,
                )
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ledger_lock_refreshes_a_previously_loaded_counter(
    tmp_path: Path, backend: str
) -> None:
    url = os.environ.get("WOS_DATABASE_URL", "")
    if backend == "postgres" and not url.startswith("postgresql"):
        pytest.skip("Requires isolated PostgreSQL CI service")
    if backend == "sqlite":
        url = f"sqlite+aiosqlite:///{tmp_path / 'counter.db'}"
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    if backend == "sqlite":
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as session, session.begin():
            session.add(StorageLedger(id=1))
    initial = 0
    try:
        async with sessions() as reader:
            stale = await reader.get(StorageLedger, 1)
            assert stale is not None
            initial = stale.managed_bytes
            await reader.commit()
            async with sessions() as writer, writer.begin():
                current = await accounting.lock_storage_ledger(writer, now=datetime.now(UTC))
                current.managed_bytes += 1
            refreshed = await accounting.lock_storage_ledger(reader, now=datetime.now(UTC))
            assert refreshed is stale
            assert refreshed.managed_bytes == initial + 1
            await reader.rollback()
    finally:
        async with sessions() as session, session.begin():
            restored = await session.get(StorageLedger, 1, with_for_update=True)
            assert restored is not None
            restored.managed_bytes = initial
        await engine.dispose()
