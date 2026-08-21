import asyncio
import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import ManagedTorrent, TorrentRequest, TorrentRequestState, User
from app.torrents import (
    TorrentDeduplicationError,
    TorrentMetadataConflictError,
    TorrentRequestOwnerError,
    create_or_get_torrent_request,
)

NOW = datetime(2026, 8, 21, 16, tzinfo=UTC)
INFO_HASH = "c" * 40


async def create_user(
    session: AsyncSession,
    username: str,
    *,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        password_hash="test-password-hash",
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    return user


@pytest.mark.asyncio
async def test_two_owners_share_one_managed_torrent_and_keep_two_requests(
    db_session: AsyncSession,
) -> None:
    first_user = await create_user(db_session, "dedup-first")
    second_user = await create_user(db_session, "dedup-second")

    first = await create_or_get_torrent_request(
        db_session,
        user_id=first_user.id,
        info_hash=INFO_HASH,
        name="Shared torrent",
        total_size=42,
        now=NOW,
    )
    second = await create_or_get_torrent_request(
        db_session,
        user_id=second_user.id,
        info_hash=INFO_HASH,
        name="Shared torrent",
        total_size=42,
        now=NOW,
    )
    await db_session.commit()

    managed_count = await db_session.scalar(select(func.count()).select_from(ManagedTorrent))
    request_count = await db_session.scalar(select(func.count()).select_from(TorrentRequest))
    assert first.managed_torrent_created is True
    assert second.managed_torrent_created is False
    assert first.request_created is second.request_created is True
    assert first.managed_torrent.id == second.managed_torrent.id
    assert first.managed_torrent.storage_key == second.managed_torrent.storage_key
    assert first.request.user_id != second.request.user_id
    assert managed_count == 1
    assert request_count == 2


@pytest.mark.asyncio
async def test_same_owner_and_infohash_are_idempotent(db_session: AsyncSession) -> None:
    user = await create_user(db_session, "dedup-repeat")
    first = await create_or_get_torrent_request(
        db_session,
        user_id=user.id,
        info_hash=INFO_HASH,
        name="Repeated torrent",
        total_size=10,
    )
    second = await create_or_get_torrent_request(
        db_session,
        user_id=user.id,
        info_hash=INFO_HASH,
        name="Repeated torrent",
        total_size=10,
    )

    assert first.request_created is True
    assert second.request_created is False
    assert first.request.id == second.request.id
    assert await db_session.scalar(select(func.count()).select_from(TorrentRequest)) == 1


@pytest.mark.asyncio
async def test_terminal_request_allows_a_new_active_right(db_session: AsyncSession) -> None:
    user = await create_user(db_session, "dedup-renew")
    first = await create_or_get_torrent_request(
        db_session,
        user_id=user.id,
        info_hash=INFO_HASH,
        name="Renewed torrent",
        total_size=10,
        now=NOW,
    )
    first.request.state = TorrentRequestState.CANCELLED
    first.request.cancelled_at = NOW
    await db_session.flush()

    second = await create_or_get_torrent_request(
        db_session,
        user_id=user.id,
        info_hash=INFO_HASH,
        name="Renewed torrent",
        total_size=10,
        now=NOW,
    )

    assert second.request_created is True
    assert second.request.id != first.request.id
    assert second.managed_torrent.id == first.managed_torrent.id
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(TorrentRequest)) == 2


@pytest.mark.asyncio
async def test_conflicting_metadata_for_existing_infohash_is_rejected(
    db_session: AsyncSession,
) -> None:
    first_user = await create_user(db_session, "dedup-source")
    second_user = await create_user(db_session, "dedup-conflict")
    await create_or_get_torrent_request(
        db_session,
        user_id=first_user.id,
        info_hash=INFO_HASH,
        name="Canonical torrent",
        total_size=10,
    )

    with pytest.raises(TorrentMetadataConflictError):
        await create_or_get_torrent_request(
            db_session,
            user_id=second_user.id,
            info_hash=INFO_HASH,
            name="Canonical torrent",
            total_size=11,
        )

    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(TorrentRequest)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("info_hash", "name", "total_size", "now"),
    [
        ("C" * 40, "Torrent", 1, NOW),
        ("c" * 39, "Torrent", 1, NOW),
        (INFO_HASH, "", 1, NOW),
        (INFO_HASH, "bad\x00name", 1, NOW),
        (INFO_HASH, "Torrent", -1, NOW),
        (INFO_HASH, "Torrent", True, NOW),
        (INFO_HASH, "Torrent", 1, datetime(2026, 8, 21, 16)),
    ],
)
async def test_invalid_canonical_metadata_is_rejected_before_sql_writes(
    db_session: AsyncSession,
    info_hash: str,
    name: str,
    total_size: int,
    now: datetime,
) -> None:
    user = await create_user(db_session, f"invalid-{uuid.uuid4().hex[:8]}")

    with pytest.raises(TorrentDeduplicationError):
        await create_or_get_torrent_request(
            db_session,
            user_id=user.id,
            info_hash=info_hash,
            name=name,
            total_size=total_size,
            now=now,
        )

    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("is_active", "missing"), [(False, False), (True, True)])
async def test_request_owner_must_exist_and_be_active(
    db_session: AsyncSession,
    is_active: bool,
    missing: bool,
) -> None:
    user_id = uuid.uuid4()
    if not missing:
        user = await create_user(db_session, "inactive-owner", is_active=is_active)
        user_id = user.id

    with pytest.raises(TorrentRequestOwnerError):
        await create_or_get_torrent_request(
            db_session,
            user_id=user_id,
            info_hash=INFO_HASH,
            name="Owner validation",
            total_size=1,
        )

    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0


@pytest.mark.asyncio
async def test_service_leaves_commit_and_rollback_to_the_caller(db_session: AsyncSession) -> None:
    user = await create_user(db_session, "rollback-owner")
    await create_or_get_torrent_request(
        db_session,
        user_id=user.id,
        info_hash=INFO_HASH,
        name="Rolled back torrent",
        total_size=1,
    )
    await db_session.rollback()

    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0
    assert await db_session.scalar(select(func.count()).select_from(TorrentRequest)) == 0


@pytest.mark.asyncio
async def test_postgresql_concurrent_owners_converge_on_one_managed_torrent() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL deduplication test requires WOS_DATABASE_URL")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    info_hash = (suffix + "0" * 8)[:40]
    user_ids: list[uuid.UUID] = []
    managed_id: uuid.UUID | None = None
    try:
        async with session_factory() as seed_session:
            users = [
                User(
                    username=f"dedup-{suffix[:8]}-a",
                    password_hash="test-password-hash",
                ),
                User(
                    username=f"dedup-{suffix[:8]}-b",
                    password_hash="test-password-hash",
                ),
            ]
            seed_session.add_all(users)
            await seed_session.flush()
            user_ids = [user.id for user in users]
            await seed_session.commit()

        async def submit(user_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
            async with session_factory() as session:
                result = await create_or_get_torrent_request(
                    session,
                    user_id=user_id,
                    info_hash=info_hash,
                    name="Concurrent shared torrent",
                    total_size=100,
                    now=NOW,
                )
                await session.commit()
                return result.managed_torrent.id, result.request.id

        first, second = await asyncio.gather(*(submit(user_id) for user_id in user_ids))
        managed_id = first[0]

        assert first[0] == second[0]
        assert first[1] != second[1]
        async with session_factory() as verification_session:
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(ManagedTorrent)
                    .where(ManagedTorrent.info_hash == info_hash)
                )
                == 1
            )
            assert (
                await verification_session.scalar(
                    select(func.count())
                    .select_from(TorrentRequest)
                    .where(TorrentRequest.managed_torrent_id == managed_id)
                )
                == 2
            )
    finally:
        async with session_factory() as cleanup_session:
            if managed_id is not None:
                await cleanup_session.execute(
                    delete(ManagedTorrent).where(ManagedTorrent.id == managed_id)
                )
            if user_ids:
                await cleanup_session.execute(delete(User).where(User.id.in_(user_ids)))
            await cleanup_session.commit()
        await engine.dispose()
