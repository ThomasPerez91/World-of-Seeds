import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_storage_admission_concurrency import postgres_storage as postgres_storage
from test_v2_file_downloads import _ready_file

from app.auth.service import ManagedUserNotFoundError, delete_managed_user, set_managed_user_active
from app.models import (
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
    UserStorageUsage,
)
from app.torrents import create_or_get_torrent_request
from app.torrents.deduplication import TorrentRequestOwnerError
from app.torrents.downloads import DownloadLeaseManager, ManagedDownloadError


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["acquire", "renew", "upload"])
@pytest.mark.parametrize("revocation", ["suspend", "delete"])
async def test_loaded_owner_is_revalidated_after_revocation(
    db_session: AsyncSession,
    data_root: Path,
    operation: str,
    revocation: str,
) -> None:
    owner, torrent, request, file, _ = await _ready_file(db_session, data_root)
    now = datetime(2026, 10, 13, tzinfo=UTC)
    manager = DownloadLeaseManager(db_session, lease_seconds=60, clock=lambda: now)
    ids = owner.id, torrent.id, request.id, file.id

    async def acquire() -> DownloadLease:
        return await manager.acquire(
            user_id=ids[0],
            managed_torrent_id=ids[1],
            torrent_request_id=ids[2],
            torrent_file_id=ids[3],
            max_concurrent=2,
        )

    lease = await acquire() if operation == "renew" else None
    # Retain the identity authenticated before a separate administrator commit.
    async with AsyncSession(bind=db_session.bind) as writer:
        await writer.execute(
            update(User)
            .where(User.id == ids[0])
            .values(is_active=False, deleted_at=now if revocation == "delete" else None)
        )
        await writer.commit()
    assert owner.is_active is True
    if operation == "upload":
        with pytest.raises(TorrentRequestOwnerError):
            await create_or_get_torrent_request(
                db_session,
                user_id=ids[0],
                info_hash=torrent.info_hash,
                name=torrent.name,
                total_size=torrent.total_size,
            )
        await db_session.rollback()
    elif operation == "acquire":
        with pytest.raises(ManagedDownloadError):
            await acquire()
        assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0
    else:
        assert lease is not None
        with pytest.raises(ManagedDownloadError):
            await manager.renew(lease.id)
    assert (
        await db_session.scalar(select(TorrentRequest.state).where(TorrentRequest.id == ids[2]))
    ) is TorrentRequestState.READY


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "resume"])
async def test_deleted_loaded_user_cannot_be_mutated_again(
    db_session: AsyncSession,
    operation: str,
) -> None:
    owner = User(username="already-deleted", password_hash="hash")
    db_session.add(owner)
    await db_session.commit()
    owner_id = owner.id
    async with AsyncSession(bind=db_session.bind) as writer:
        await writer.execute(
            update(User)
            .where(User.id == owner_id)
            .values(is_active=False, deleted_at=datetime.now(UTC))
        )
        await writer.commit()
    assert owner.deleted_at is None
    with pytest.raises(ManagedUserNotFoundError):
        if operation == "delete":
            await delete_managed_user(db_session, user_id=owner_id)
        else:
            await set_managed_user_active(db_session, user_id=owner_id, is_active=True)
    await db_session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["delete", "suspend"])
async def test_account_revocation_preserves_other_owner_download(
    db_session: AsyncSession,
    data_root: Path,
    operation: str,
) -> None:
    owner, torrent, request, file, _ = await _ready_file(db_session, data_root)
    other = User(username="other-owner", password_hash="hash")
    other_request = TorrentRequest(
        user=other,
        managed_torrent=torrent,
        state=TorrentRequestState.READY,
        unsubscribe_at=request.unsubscribe_at,
    )
    own_usage = UserStorageUsage(user=owner, logical_bytes=torrent.total_size)
    other_usage = UserStorageUsage(user=other, logical_bytes=torrent.total_size)
    db_session.add_all([other_request, own_usage, other_usage])
    await db_session.commit()
    manager = DownloadLeaseManager(db_session, lease_seconds=60)
    lease = await manager.acquire(
        user_id=other.id,
        managed_torrent_id=torrent.id,
        torrent_request_id=other_request.id,
        torrent_file_id=file.id,
        max_concurrent=2,
    )
    if operation == "delete":
        await delete_managed_user(db_session, user_id=owner.id)
        assert request.state is TorrentRequestState.CANCELLED
        assert own_usage.logical_bytes == 0
    else:
        await set_managed_user_active(db_session, user_id=owner.id, is_active=False)
        assert request.state is TorrentRequestState.READY
        assert own_usage.logical_bytes == torrent.total_size
    assert other_request.state is TorrentRequestState.READY
    assert other_usage.logical_bytes == torrent.total_size
    assert torrent.purge_after is None
    await db_session.commit()
    await manager.renew(lease.id)
    second = await manager.acquire(
        user_id=other.id,
        managed_torrent_id=torrent.id,
        torrent_request_id=other_request.id,
        torrent_file_id=file.id,
        max_concurrent=2,
    )
    assert second.id != lease.id


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["suspend", "delete"])
async def test_download_waiting_on_account_sees_admin_revocation(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
    revocation: str,
) -> None:
    sessions, prefix = postgres_storage
    now = datetime.now(UTC)
    async with sessions() as seed:
        owner = User(username=prefix, password_hash="hash")
        torrent = ManagedTorrent(
            info_hash=uuid.uuid4().hex + "00000000",
            name=prefix,
            total_size=1,
            state=ManagedTorrentState.READY,
        )
        request = TorrentRequest(
            user=owner,
            managed_torrent=torrent,
            state=TorrentRequestState.READY,
            unsubscribe_at=now + timedelta(days=2),
        )
        file = TorrentFile(managed_torrent=torrent, file_index=0, relative_path="test.bin", size=1)
        seed.add_all([request, file])
        await seed.commit()
        ids = owner.id, torrent.id, request.id, file.id
    reached = asyncio.Event()
    original_scalar = AsyncSession.scalar
    task = None

    async def scalar(session: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            asyncio.current_task() is task
            and statement.column_descriptions[0].get("entity") is User
        ):
            reached.set()
        return await original_scalar(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    async with sessions() as reader, sessions() as admin:
        loaded = await reader.get(User, ids[0])
        assert loaded is not None
        await reader.commit()
        locked = await admin.get(User, ids[0], with_for_update=True)
        assert locked is not None
        manager = DownloadLeaseManager(reader, lease_seconds=60)
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
            await asyncio.wait_for(reached.wait(), 2)
            locked.is_active = False
            locked.deleted_at = now if revocation == "delete" else None
            await admin.commit()
            with pytest.raises(ManagedDownloadError):
                await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with sessions() as check:
        assert (
            await check.scalar(
                select(func.count())
                .select_from(DownloadLease)
                .where(DownloadLease.user_id == ids[0])
            )
            == 0
        )


@pytest.mark.asyncio
async def test_deletion_waits_for_all_parents_before_locking_usage(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions, prefix = postgres_storage
    now = datetime.now(UTC)
    first_id = uuid.uuid4().int >> 1
    async with sessions() as seed:
        owner = User(username=prefix, password_hash="hash")
        parents = [
            ManagedTorrent(
                id=uuid.UUID(int=first_id + i),
                info_hash=uuid.uuid4().hex + "00000000",
                name=f"{prefix}-{i}",
                total_size=10,
                state=ManagedTorrentState.READY,
            )
            for i in range(2)
        ]
        requests = [
            TorrentRequest(
                user=owner,
                managed_torrent=p,
                state=TorrentRequestState.READY,
                unsubscribe_at=now + timedelta(days=2),
            )
            for p in parents
        ]
        seed.add_all(requests + [UserStorageUsage(user=owner, logical_bytes=20)])
        await seed.commit()
        owner_id, second_id = owner.id, parents[1].id
    reached = asyncio.Event()
    original_scalars = AsyncSession.scalars
    task = None

    async def scalars(session: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            asyncio.current_task() is task
            and statement.column_descriptions[0].get("entity") is ManagedTorrent
        ):
            reached.set()
        return await original_scalars(session, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "scalars", scalars)

    async def remove() -> bool:
        async with sessions() as session:
            return await delete_managed_user(session, user_id=owner_id)

    async with sessions() as expiration:
        assert await expiration.get(ManagedTorrent, second_id, with_for_update=True) is not None
        task = asyncio.create_task(remove())
        try:
            await asyncio.wait_for(reached.wait(), 2)
            # Expiration already owns a parent and must still be able to take usage.
            usage = await asyncio.wait_for(
                expiration.get(UserStorageUsage, owner_id, with_for_update=True), 2
            )
            assert usage is not None
            await expiration.commit()
            await asyncio.wait_for(task, 2)
        finally:
            await expiration.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with sessions() as check:
        usage = await check.get(UserStorageUsage, owner_id)
        assert usage is not None and usage.logical_bytes == 0
        states = (
            await check.scalars(
                select(TorrentRequest.state).where(TorrentRequest.user_id == owner_id)
            )
        ).all()
        assert states == [TorrentRequestState.CANCELLED] * 2
