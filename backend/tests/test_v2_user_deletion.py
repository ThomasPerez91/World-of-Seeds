import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import delete_managed_user, set_managed_user_active
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


@pytest.mark.asyncio
async def test_toggling_sole_waiting_owner_changes_physical_queue_membership(
    db_session: AsyncSession,
) -> None:
    owner = User(username="status-owner", password_hash="hash")
    torrent = ManagedTorrent(
        info_hash="7" * 40,
        name="Waiting",
        total_size=10,
        state=ManagedTorrentState.PAUSED,
        desired_active=False,
    )
    db_session.add(
        TorrentRequest(
            user=owner,
            managed_torrent=torrent,
            state=TorrentRequestState.ACTIVE,
        )
    )
    await db_session.commit()

    disabled = await set_managed_user_active(
        db_session,
        user_id=owner.id,
        is_active=False,
    )
    enabled = await set_managed_user_active(
        db_session,
        user_id=owner.id,
        is_active=True,
    )

    assert disabled.queue_membership_changed is True
    assert enabled.queue_membership_changed is True


@pytest.mark.asyncio
async def test_deleting_inactive_owner_does_not_invalidate_physical_queue(
    db_session: AsyncSession,
) -> None:
    await PostgresOptionsRegistry().initialize(db_session)
    owner = User(username="inactive-delete-owner", password_hash="hash", is_active=False)
    torrent = ManagedTorrent(
        info_hash="8" * 40,
        name="Inactive owner waiting torrent",
        total_size=10,
        state=ManagedTorrentState.PAUSED,
        desired_active=False,
    )
    db_session.add(
        TorrentRequest(
            user=owner,
            managed_torrent=torrent,
            state=TorrentRequestState.ACTIVE,
        )
    )
    await db_session.commit()

    queue_membership_changed = await delete_managed_user(db_session, user_id=owner.id)

    assert queue_membership_changed is False
