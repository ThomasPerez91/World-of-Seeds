from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import delete_managed_user
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.options import PostgresOptionsRegistry
from app.torrents.downloads import DownloadLeaseManager, ManagedDownloadError


@pytest.mark.asyncio
async def test_engaged_download_can_finish_after_unsubscribe_but_no_new_lease_starts(
    db_session: AsyncSession,
) -> None:
    now = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)
    owner = User(username="lease-owner", password_hash="not-used")
    torrent = ManagedTorrent(
        info_hash="a" * 40,
        name="Release readiness",
        total_size=1,
        state=ManagedTorrentState.READY,
        progress=1.0,
        ready_at=now - timedelta(hours=1),
        retention_expires_at=now + timedelta(days=2),
        manifest_version=1,
        manifest_checksum="b" * 64,
        manifest_file_count=1,
        manifest_total_size=1,
    )
    request = TorrentRequest(
        user=owner,
        managed_torrent=torrent,
        state=TorrentRequestState.READY,
    )
    torrent_file = TorrentFile(
        managed_torrent=torrent,
        file_index=0,
        relative_path="payload.bin",
        size=1,
    )
    db_session.add_all([owner, torrent, request, torrent_file])
    await db_session.commit()

    owner_id = owner.id
    managed_torrent_id = torrent.id
    torrent_request_id = request.id
    torrent_file_id = torrent_file.id

    manager = DownloadLeaseManager(db_session, lease_seconds=60, clock=lambda: now)
    lease = await manager.acquire(
        user_id=owner_id,
        managed_torrent_id=managed_torrent_id,
        torrent_request_id=torrent_request_id,
        torrent_file_id=torrent_file_id,
        max_concurrent=2,
    )
    lease_id = lease.id

    cancelled_request = await db_session.get(TorrentRequest, torrent_request_id)
    assert cancelled_request is not None
    cancelled_request.state = TorrentRequestState.CANCELLED
    await db_session.commit()

    await manager.renew(lease_id)

    with pytest.raises(ManagedDownloadError):
        await manager.acquire(
            user_id=owner_id,
            managed_torrent_id=managed_torrent_id,
            torrent_request_id=torrent_request_id,
            torrent_file_id=torrent_file_id,
            max_concurrent=2,
        )

    pending_torrent = await db_session.get(ManagedTorrent, managed_torrent_id)
    assert pending_torrent is not None
    pending_torrent.state = ManagedTorrentState.PURGE_PENDING
    pending_torrent.purge_after = now
    pending_torrent.desired_active = False
    pending_torrent.desired_priority = None
    pending_torrent.purge_stop_pending = True
    await db_session.commit()
    await manager.renew(lease_id)

    purging_torrent = await db_session.get(ManagedTorrent, managed_torrent_id)
    assert purging_torrent is not None
    purging_torrent.state = ManagedTorrentState.PURGING
    await db_session.commit()
    with pytest.raises(ManagedDownloadError):
        await manager.renew(lease_id)


@pytest.mark.asyncio
async def test_admin_deletion_revokes_an_already_engaged_download(
    db_session: AsyncSession,
) -> None:
    await PostgresOptionsRegistry().initialize(db_session)
    now = datetime(2026, 9, 9, 20, 0, tzinfo=UTC)
    owner = User(username="deleted-lease-owner", password_hash="not-used")
    torrent = ManagedTorrent(
        info_hash="c" * 40,
        name="Deleted owner release readiness",
        total_size=1,
        state=ManagedTorrentState.READY,
        progress=1.0,
        ready_at=now - timedelta(hours=1),
        retention_expires_at=now + timedelta(days=2),
        manifest_version=1,
        manifest_checksum="d" * 64,
        manifest_file_count=1,
        manifest_total_size=1,
    )
    request = TorrentRequest(
        user=owner,
        managed_torrent=torrent,
        state=TorrentRequestState.READY,
    )
    torrent_file = TorrentFile(
        managed_torrent=torrent,
        file_index=0,
        relative_path="payload.bin",
        size=1,
    )
    db_session.add_all([owner, torrent, request, torrent_file])
    await db_session.commit()

    owner_id = owner.id
    manager = DownloadLeaseManager(db_session, lease_seconds=60, clock=lambda: now)
    lease = await manager.acquire(
        user_id=owner_id,
        managed_torrent_id=torrent.id,
        torrent_request_id=request.id,
        torrent_file_id=torrent_file.id,
        max_concurrent=2,
    )
    lease_id = lease.id

    await delete_managed_user(db_session, user_id=owner_id)

    deleted_owner = await db_session.get(User, owner_id)
    assert deleted_owner is not None
    assert deleted_owner.is_active is False
    assert deleted_owner.deleted_at is not None
    with pytest.raises(ManagedDownloadError):
        await manager.renew(lease_id)
