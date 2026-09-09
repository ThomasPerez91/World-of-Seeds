from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
)
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

    manager = DownloadLeaseManager(db_session, lease_seconds=60, clock=lambda: now)
    lease = await manager.acquire(
        user_id=owner.id,
        managed_torrent_id=torrent.id,
        torrent_request_id=request.id,
        torrent_file_id=torrent_file.id,
        max_concurrent=2,
    )

    request.state = TorrentRequestState.CANCELLED
    await db_session.commit()

    await manager.renew(lease.id)
    assert lease.expires_at == now + timedelta(seconds=60)

    with pytest.raises(ManagedDownloadError):
        await manager.acquire(
            user_id=owner.id,
            managed_torrent_id=torrent.id,
            torrent_request_id=request.id,
            torrent_file_id=torrent_file.id,
            max_concurrent=2,
        )

    torrent.state = ManagedTorrentState.PURGE_PENDING
    torrent.purge_after = now
    torrent.desired_active = False
    torrent.desired_priority = None
    torrent.purge_stop_pending = True
    await db_session.commit()
    await manager.renew(lease.id)

    torrent.state = ManagedTorrentState.PURGING
    await db_session.commit()
    with pytest.raises(ManagedDownloadError):
        await manager.renew(lease.id)
