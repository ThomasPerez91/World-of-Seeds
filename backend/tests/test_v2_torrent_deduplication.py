import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    StorageLedger,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    User,
    UserStorageUsage,
)
from app.torrents import (
    TorrentDeduplicationError,
    TorrentMetadataConflictError,
    TorrentPurgeInProgressError,
    TorrentRequestOwnerError,
    cancel_owned_torrent_request,
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
    ledger = await db_session.get(StorageLedger, 1)
    first_usage = await db_session.get(UserStorageUsage, first_user.id)
    second_usage = await db_session.get(UserStorageUsage, second_user.id)
    assert ledger is not None and ledger.managed_bytes == 42
    assert first_usage is not None and first_usage.logical_bytes == 42
    assert second_usage is not None and second_usage.logical_bytes == 42


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
    usage = await db_session.get(UserStorageUsage, user.id)
    ledger = await db_session.get(StorageLedger, 1)
    assert usage is not None and usage.logical_bytes == 10
    assert ledger is not None and ledger.managed_bytes == 10


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
async def test_last_cancellation_schedules_purge_and_new_owner_revokes_it(
    db_session: AsyncSession,
) -> None:
    first_user = await create_user(db_session, "lifecycle-first")
    second_user = await create_user(db_session, "lifecycle-second")
    first = await create_or_get_torrent_request(
        db_session,
        user_id=first_user.id,
        info_hash=INFO_HASH,
        name="Lifecycle torrent",
        total_size=10,
        now=NOW,
    )
    second = await create_or_get_torrent_request(
        db_session,
        user_id=second_user.id,
        info_hash=INFO_HASH,
        name="Lifecycle torrent",
        total_size=10,
        now=NOW,
    )
    first.managed_torrent.state = ManagedTorrentState.READY
    first.managed_torrent.progress = 1
    first.request.state = second.request.state = TorrentRequestState.READY
    await db_session.flush()

    first_cancelled = await cancel_owned_torrent_request(
        db_session,
        user_id=first_user.id,
        torrent_request_id=first.request.id,
        retention_hours=48,
        now=NOW,
    )
    second_cancelled = await cancel_owned_torrent_request(
        db_session,
        user_id=second_user.id,
        torrent_request_id=second.request.id,
        retention_hours=48,
        now=NOW,
    )

    assert first_cancelled is not None and first_cancelled.purge_scheduled is False
    assert second_cancelled is not None and second_cancelled.purge_scheduled is True
    assert first.managed_torrent.state is ManagedTorrentState.PURGE_PENDING
    assert first.managed_torrent.purge_stop_pending is True
    purge = await db_session.scalar(
        select(TorrentJob).where(TorrentJob.job_type == "PURGE_TORRENT")
    )
    assert purge is not None and purge.state is TorrentJobState.QUEUED
    purge_id = purge.id

    renewed = await create_or_get_torrent_request(
        db_session,
        user_id=first_user.id,
        info_hash=INFO_HASH,
        name="Lifecycle torrent",
        total_size=10,
        now=NOW,
    )

    assert renewed.request_created is True
    assert renewed.request.state is TorrentRequestState.READY
    assert renewed.managed_torrent.state is ManagedTorrentState.READY
    assert renewed.managed_torrent.purge_after is None
    assert renewed.managed_torrent.purge_stop_pending is False
    purge_state = await db_session.scalar(select(TorrentJob.state).where(TorrentJob.id == purge_id))
    assert purge_state is TorrentJobState.CANCELLED


@pytest.mark.asyncio
async def test_last_owner_cancelling_downloading_torrent_persists_scheduler_stop(
    db_session: AsyncSession,
) -> None:
    owner = await create_user(db_session, "downloading-cancel-owner")
    created = await create_or_get_torrent_request(
        db_session,
        user_id=owner.id,
        info_hash="d" * 40,
        name="Partial download",
        total_size=100,
        now=NOW,
    )
    created.managed_torrent.state = ManagedTorrentState.DOWNLOADING
    created.managed_torrent.progress = 0.4
    created.managed_torrent.desired_active = True
    created.managed_torrent.desired_priority = 0
    created.request.state = TorrentRequestState.ACTIVE
    await db_session.flush()

    cancelled = await cancel_owned_torrent_request(
        db_session,
        user_id=owner.id,
        torrent_request_id=created.request.id,
        retention_hours=48,
        now=NOW,
    )

    assert cancelled is not None and cancelled.purge_scheduled is True
    assert created.managed_torrent.state is ManagedTorrentState.PURGE_PENDING
    assert created.managed_torrent.desired_active is False
    assert created.managed_torrent.desired_priority is None
    assert created.managed_torrent.purge_stop_pending is True
    assert created.managed_torrent.progress == 0.4


@pytest.mark.asyncio
async def test_new_owner_reactivating_retained_partial_torrent_changes_queue_membership(
    db_session: AsyncSession,
) -> None:
    first_owner = await create_user(db_session, "retained-partial-first")
    second_owner = await create_user(db_session, "retained-partial-second")
    created = await create_or_get_torrent_request(
        db_session,
        user_id=first_owner.id,
        info_hash="f" * 40,
        name="Retained partial",
        total_size=100,
        now=NOW,
    )
    created.managed_torrent.state = ManagedTorrentState.DOWNLOADING
    created.managed_torrent.progress = 0.4
    created.managed_torrent.desired_active = True
    created.managed_torrent.desired_priority = 0
    created.request.state = TorrentRequestState.ACTIVE
    await db_session.flush()
    cancelled = await cancel_owned_torrent_request(
        db_session,
        user_id=first_owner.id,
        torrent_request_id=created.request.id,
        retention_hours=48,
        now=NOW,
    )
    assert cancelled is not None and cancelled.purge_scheduled is True

    resumed = await create_or_get_torrent_request(
        db_session,
        user_id=second_owner.id,
        info_hash="f" * 40,
        name="Retained partial",
        total_size=100,
        now=NOW + timedelta(seconds=1),
    )

    assert resumed.managed_torrent.state is ManagedTorrentState.DOWNLOADING
    assert resumed.managed_torrent.desired_active is False
    assert resumed.request.state is TorrentRequestState.ACTIVE
    assert resumed.queue_membership_changed is True


@pytest.mark.asyncio
async def test_new_request_is_rejected_while_physical_purge_owns_lifecycle(
    db_session: AsyncSession,
) -> None:
    first_user = await create_user(db_session, "purging-first")
    second_user = await create_user(db_session, "purging-second")
    first = await create_or_get_torrent_request(
        db_session,
        user_id=first_user.id,
        info_hash=INFO_HASH,
        name="Purging torrent",
        total_size=10,
        now=NOW,
    )
    first.request.state = TorrentRequestState.CANCELLED
    first.managed_torrent.state = ManagedTorrentState.PURGING
    first.managed_torrent.purge_after = NOW
    await db_session.flush()

    with pytest.raises(TorrentPurgeInProgressError):
        await create_or_get_torrent_request(
            db_session,
            user_id=second_user.id,
            info_hash=INFO_HASH,
            name="Purging torrent",
            total_size=10,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_purged_content_can_be_reactivated_as_one_new_physical_copy(
    db_session: AsyncSession,
) -> None:
    owner = await create_user(db_session, "purged-owner")
    first = await create_or_get_torrent_request(
        db_session,
        user_id=owner.id,
        info_hash=INFO_HASH,
        name="Purged torrent",
        total_size=10,
        now=NOW,
    )
    first.request.state = TorrentRequestState.CANCELLED
    first.managed_torrent.state = ManagedTorrentState.PURGED
    ledger = await db_session.get(StorageLedger, 1)
    assert ledger is not None
    ledger.managed_bytes = 0
    await db_session.flush()

    renewed = await create_or_get_torrent_request(
        db_session,
        user_id=owner.id,
        info_hash=INFO_HASH,
        name="Purged torrent",
        total_size=10,
        now=NOW,
    )

    assert renewed.managed_torrent_created is False
    assert renewed.managed_torrent_reactivated is True
    assert renewed.managed_torrent.state is ManagedTorrentState.PENDING
    assert renewed.request.state is TorrentRequestState.REQUESTED
    assert ledger.managed_bytes == 10


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
            ledger = await verification_session.get(StorageLedger, 1)
            usages = [
                await verification_session.get(UserStorageUsage, user_id) for user_id in user_ids
            ]
            assert ledger is not None and ledger.managed_bytes >= 100
            assert all(usage is not None and usage.logical_bytes == 100 for usage in usages)
    finally:
        async with session_factory() as cleanup_session:
            if managed_id is not None:
                await cleanup_session.execute(
                    delete(ManagedTorrent).where(ManagedTorrent.id == managed_id)
                )
                ledger = await cleanup_session.get(StorageLedger, 1, with_for_update=True)
                if ledger is not None:
                    ledger.managed_bytes = max(0, ledger.managed_bytes - 100)
            if user_ids:
                await cleanup_session.execute(delete(User).where(User.id.in_(user_ids)))
            await cleanup_session.commit()
        await engine.dispose()
