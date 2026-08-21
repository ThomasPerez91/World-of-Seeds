import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
)


async def create_user(db_session: AsyncSession, username: str) -> User:
    user = User(username=username, password_hash="test-password-hash")
    db_session.add(user)
    await db_session.flush()
    return user


async def create_managed_torrent(
    db_session: AsyncSession,
    *,
    info_hash: str = "a" * 40,
) -> ManagedTorrent:
    torrent = ManagedTorrent(info_hash=info_hash, name="Example", total_size=10)
    db_session.add(torrent)
    await db_session.flush()
    return torrent


def test_v2_torrent_states_match_the_normative_state_machines() -> None:
    assert {state.value for state in ManagedTorrentState} == {
        "PENDING",
        "ADDING",
        "DOWNLOADING",
        "PAUSED",
        "RETRY_WAIT",
        "ERROR",
        "READY",
        "PURGE_PENDING",
        "PURGED",
    }
    assert {state.value for state in TorrentRequestState} == {
        "REQUESTED",
        "ACTIVE",
        "READY",
        "CANCELLED",
        "EXPIRED",
    }


@pytest.mark.asyncio
async def test_info_hash_identifies_one_managed_torrent(db_session: AsyncSession) -> None:
    await create_managed_torrent(db_session)
    await db_session.commit()

    db_session.add(ManagedTorrent(info_hash="a" * 40, name="Duplicate", total_size=10))
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_info_hash_must_be_canonical(db_session: AsyncSession) -> None:
    db_session.add(ManagedTorrent(info_hash="A" * 40, name="Uppercase", total_size=10))

    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_shared_torrent_has_one_active_request_per_owner(
    db_session: AsyncSession,
) -> None:
    first_user = await create_user(db_session, "first-user")
    second_user = await create_user(db_session, "second-user")
    torrent = await create_managed_torrent(db_session)
    first_request = TorrentRequest(user=first_user, managed_torrent=torrent)
    db_session.add_all(
        [
            first_request,
            TorrentRequest(user=second_user, managed_torrent=torrent),
        ]
    )
    await db_session.commit()
    first_user_id = first_user.id
    torrent_id = torrent.id
    first_request_id = first_request.id

    db_session.add(TorrentRequest(user_id=first_user_id, managed_torrent_id=torrent_id))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    stored_request = await db_session.get(TorrentRequest, first_request_id)
    assert stored_request is not None
    stored_request.state = TorrentRequestState.CANCELLED
    db_session.add(TorrentRequest(user_id=first_user_id, managed_torrent_id=torrent_id))
    await db_session.commit()

    request_count = await db_session.scalar(select(func.count()).select_from(TorrentRequest))
    assert request_count == 3


@pytest.mark.asyncio
async def test_torrent_file_manifest_is_unique_per_torrent(db_session: AsyncSession) -> None:
    torrent = await create_managed_torrent(db_session)
    db_session.add(
        TorrentFile(
            managed_torrent=torrent,
            file_index=0,
            relative_path="season/episode.mkv",
            size=10,
        )
    )
    await db_session.commit()

    db_session.add(
        TorrentFile(
            managed_torrent_id=torrent.id,
            file_index=1,
            relative_path="season/episode.mkv",
            size=10,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("relative_path", ["", "/absolute", "..", "../escape", "a/../escape"])
async def test_torrent_file_rejects_unsafe_relative_paths(
    db_session: AsyncSession,
    relative_path: str,
) -> None:
    torrent = await create_managed_torrent(db_session)
    db_session.add(
        TorrentFile(
            managed_torrent=torrent,
            file_index=0,
            relative_path=relative_path,
            size=10,
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.commit()
