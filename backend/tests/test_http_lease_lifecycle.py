import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_storage_admission_concurrency import postgres_storage as postgres_storage
from test_v2_file_downloads import _ready_file

from app.models import (
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.options import PostgresOptionsRegistry
from app.torrents.downloads import DownloadLeaseManager, ManagedDownloadError
from app.torrents.lifecycle import expire_ready_torrents_batch

NOW = datetime(2026, 10, 13, tzinfo=UTC)


@pytest.mark.asyncio
async def test_acquisition_rechecks_deadline_after_right_lock(
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner, torrent, request, file, _ = await _ready_file(db_session, data_root)
    request.unsubscribe_at = NOW + timedelta(seconds=5)
    await db_session.commit()
    clock = [NOW]
    original = AsyncSession.scalar

    async def scalar(session: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        result = await original(session, statement, *args, **kwargs)
        if (
            session is db_session
            and statement.column_descriptions[0].get("entity") is TorrentRequest
        ):
            clock[0] = NOW + timedelta(seconds=6)
        return result

    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    manager = DownloadLeaseManager(db_session, lease_seconds=60, clock=lambda: clock[0])
    with pytest.raises(ManagedDownloadError):
        await manager.acquire(
            user_id=owner.id,
            managed_torrent_id=torrent.id,
            torrent_request_id=request.id,
            torrent_file_id=file.id,
            max_concurrent=2,
        )
    assert (await db_session.scalars(select(DownloadLease))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("already_expired", [False, True])
async def test_renewal_cannot_resurrect_expired_lease(
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    already_expired: bool,
) -> None:
    owner, torrent, request, file, _ = await _ready_file(db_session, data_root)
    clock = [NOW]
    manager = DownloadLeaseManager(db_session, lease_seconds=60, clock=lambda: clock[0])
    lease = await manager.acquire(
        user_id=owner.id,
        managed_torrent_id=torrent.id,
        torrent_request_id=request.id,
        torrent_file_id=file.id,
        max_concurrent=2,
    )
    lease_id, deadline = lease.id, lease.expires_at
    if already_expired:
        clock[0] = deadline
    original = AsyncSession.get

    async def get(session: AsyncSession, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
        result = await original(session, entity, ident, *args, **kwargs)
        if session is db_session and entity is DownloadLease and kwargs.get("with_for_update"):
            clock[0] = deadline
        return result

    monkeypatch.setattr(AsyncSession, "get", get)
    with pytest.raises(ManagedDownloadError):
        await manager.renew(lease_id)
    persisted = await db_session.get(DownloadLease, lease_id)
    assert persisted is not None
    assert persisted.expires_at.replace(tzinfo=UTC) == deadline


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [TorrentRequestState.EXPIRED, TorrentRequestState.CANCELLED])
@pytest.mark.parametrize("pending", [False, True])
async def test_live_transfer_retains_lease_after_unsubscribe(
    db_session: AsyncSession,
    data_root: Path,
    state: TorrentRequestState,
    pending: bool,
) -> None:
    owner, torrent, request, file, _ = await _ready_file(db_session, data_root)
    clock = [NOW]
    manager = DownloadLeaseManager(db_session, lease_seconds=60, clock=lambda: clock[0])
    ids = owner.id, torrent.id, request.id, file.id
    lease = await manager.acquire(
        user_id=ids[0],
        managed_torrent_id=ids[1],
        torrent_request_id=ids[2],
        torrent_file_id=ids[3],
        max_concurrent=2,
    )
    request.state = state
    if pending:
        torrent.state = ManagedTorrentState.PURGE_PENDING
        torrent.purge_after = NOW
    await db_session.commit()
    clock[0] = NOW + timedelta(seconds=20)
    await manager.renew(lease.id)
    assert lease.expires_at == clock[0] + timedelta(seconds=60)
    with pytest.raises(ManagedDownloadError):
        await manager.acquire(
            user_id=ids[0],
            managed_torrent_id=ids[1],
            torrent_request_id=ids[2],
            torrent_file_id=ids[3],
            max_concurrent=2,
        )


async def seed_ready(
    sessions: async_sessionmaker[AsyncSession], prefix: str
) -> tuple[uuid.UUID, ...]:
    async with sessions() as session:
        await PostgresOptionsRegistry().initialize(session)
        owner = User(username=prefix, password_hash="hash")
        torrent = ManagedTorrent(
            name=prefix,
            info_hash=uuid.uuid4().hex + "00000000",
            total_size=1,
            state=ManagedTorrentState.READY,
        )
        request = TorrentRequest(
            user=owner,
            managed_torrent=torrent,
            state=TorrentRequestState.READY,
            unsubscribe_at=NOW + timedelta(days=1),
        )
        file = TorrentFile(managed_torrent=torrent, file_index=0, relative_path="test.bin", size=1)
        session.add_all([request, file])
        await session.commit()
        return owner.id, torrent.id, request.id, file.id


@pytest.mark.asyncio
async def test_admission_waits_on_parent_without_holding_right(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, prefix = postgres_storage
    ids = await seed_ready(sessions, prefix)
    at_parent = asyncio.Event()
    reader_session = None
    original = AsyncSession.get

    async def get(session: AsyncSession, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
        if session is reader_session and entity is ManagedTorrent and kwargs.get("with_for_update"):
            at_parent.set()
        return await original(session, entity, ident, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", get)
    async with sessions() as reader, sessions() as expiration:
        reader_session = reader
        assert await expiration.get(ManagedTorrent, ids[1], with_for_update=True) is not None
        manager = DownloadLeaseManager(reader, lease_seconds=60, clock=lambda: NOW)
        task = asyncio.create_task(
            manager.acquire(
                user_id=ids[0],
                managed_torrent_id=ids[1],
                torrent_request_id=ids[2],
                torrent_file_id=ids[3],
                max_concurrent=2,
            )
        )
        try:
            await asyncio.wait_for(at_parent.wait(), 2)
            right = await asyncio.wait_for(
                expiration.get(TorrentRequest, ids[2], with_for_update=True), 2
            )
            assert right is not None
            right.unsubscribe_at = NOW - timedelta(seconds=1)
            await expiration.flush()
            results = await expire_ready_torrents_batch(expiration, now=NOW)
            assert any(r.managed_torrent_id == ids[1] for r in results)
            await expiration.commit()
            with pytest.raises(ManagedDownloadError):
                await asyncio.wait_for(task, 2)
        finally:
            await expiration.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with sessions() as check:
        assert (
            await check.scalar(select(DownloadLease.id).where(DownloadLease.user_id == ids[0]))
            is None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["expired_lease", "expired_right", "purging"])
async def test_renewal_revalidates_after_parent_wait(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    sessions, prefix = postgres_storage
    ids = await seed_ready(sessions, prefix)
    clock = [NOW]
    async with sessions() as seed:
        lease = await DownloadLeaseManager(seed, lease_seconds=60, clock=lambda: clock[0]).acquire(
            user_id=ids[0],
            managed_torrent_id=ids[1],
            torrent_request_id=ids[2],
            torrent_file_id=ids[3],
            max_concurrent=2,
        )
        lease_id = lease.id
    at_parent = asyncio.Event()
    reader_session = None
    original = AsyncSession.get

    async def get(session: AsyncSession, entity: Any, ident: Any, *args: Any, **kwargs: Any) -> Any:
        if session is reader_session and entity is ManagedTorrent and kwargs.get("with_for_update"):
            at_parent.set()
        return await original(session, entity, ident, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", get)
    async with sessions() as reader, sessions() as lifecycle:
        reader_session = reader
        parent = await lifecycle.get(ManagedTorrent, ids[1], with_for_update=True)
        assert parent is not None
        manager = DownloadLeaseManager(reader, lease_seconds=60, clock=lambda: clock[0])
        task = asyncio.create_task(manager.renew(lease_id))
        try:
            await asyncio.wait_for(at_parent.wait(), 2)
            if outcome == "expired_lease":
                clock[0] = NOW + timedelta(seconds=60)
            else:
                right = await lifecycle.get(TorrentRequest, ids[2], with_for_update=True)
                assert right is not None
                right.unsubscribe_at = NOW - timedelta(seconds=1)
                await lifecycle.flush()
                await expire_ready_torrents_batch(lifecycle, now=NOW)
                if outcome == "purging":
                    parent.state = ManagedTorrentState.PURGING
                    parent.purge_stop_pending = False
                clock[0] = NOW + timedelta(seconds=20)
            await lifecycle.commit()
            if outcome == "expired_right":
                await asyncio.wait_for(task, 2)
            else:
                with pytest.raises(ManagedDownloadError):
                    await asyncio.wait_for(task, 2)
        finally:
            await lifecycle.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with sessions() as check:
        persisted = await check.get(DownloadLease, lease_id)
        assert persisted is not None
        expected = NOW + timedelta(seconds=80 if outcome == "expired_right" else 60)
        assert persisted.expires_at == expected
