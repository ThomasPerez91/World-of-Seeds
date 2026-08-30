import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import delete_managed_user
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentRequest,
    TorrentRequestState,
    User,
    UserStorageUsage,
)
from app.options import PostgresOptionsRegistry


@pytest.mark.asyncio
async def test_deleting_user_cancels_rights_and_preserves_shared_content(
    db_session: AsyncSession,
) -> None:
    await PostgresOptionsRegistry().initialize(db_session)
    deleted_owner = User(username="deleted-owner", password_hash="hash")
    surviving_owner = User(username="surviving-owner", password_hash="hash")
    torrent = ManagedTorrent(
        info_hash="6" * 40,
        storage_key=uuid.uuid4(),
        name="Shared",
        total_size=10,
        state=ManagedTorrentState.READY,
    )
    deleted_request = TorrentRequest(
        user=deleted_owner,
        managed_torrent=torrent,
        state=TorrentRequestState.READY,
    )
    surviving_request = TorrentRequest(
        user=surviving_owner,
        managed_torrent=torrent,
        state=TorrentRequestState.READY,
    )
    deleted_usage = UserStorageUsage(user=deleted_owner, logical_bytes=10)
    surviving_usage = UserStorageUsage(user=surviving_owner, logical_bytes=10)
    db_session.add_all([deleted_request, surviving_request, deleted_usage, surviving_usage])
    await db_session.commit()

    await delete_managed_user(db_session, user_id=deleted_owner.id)

    assert deleted_owner.deleted_at is not None
    assert deleted_request.state is TorrentRequestState.CANCELLED
    assert deleted_usage.logical_bytes == 0
    assert surviving_request.state is TorrentRequestState.READY
    assert surviving_usage.logical_bytes == 10
    assert torrent.state is ManagedTorrentState.READY
    assert torrent.purge_after is None
