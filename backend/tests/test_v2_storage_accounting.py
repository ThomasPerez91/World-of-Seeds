from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StorageLedger, StoragePressureState, User, UserStorageUsage
from app.storage import (
    StorageAdmissionError,
    StorageAdmissionPolicy,
    StorageDiskSnapshot,
    reconcile_storage_counters,
)
from app.torrents import ManagedTorrentRequestResult, create_or_get_torrent_request

NOW = datetime(2026, 8, 21, 22, tzinfo=UTC)
DEFAULT_DISK = StorageDiskSnapshot(1_000, 500)


def _policy(
    *,
    managed: int = 0,
    user: int = 0,
    minimum_free: int = 0,
    minimum_free_percent: int = 0,
) -> StorageAdmissionPolicy:
    return StorageAdmissionPolicy(
        managed_max_bytes=managed,
        user_max_bytes=user,
        min_free_bytes=minimum_free,
        min_free_percent=minimum_free_percent,
        warning_percent=80,
        critical_percent=90,
    )


async def _user(session: AsyncSession, name: str) -> User:
    user = User(username=name, password_hash="hash")
    session.add(user)
    await session.flush()
    return user


async def _request(
    session: AsyncSession,
    user: User,
    *,
    info_hash: str,
    size: int,
    policy: StorageAdmissionPolicy,
    disk: StorageDiskSnapshot = DEFAULT_DISK,
) -> ManagedTorrentRequestResult:
    return await create_or_get_torrent_request(
        session,
        user_id=user.id,
        info_hash=info_hash,
        name=f"torrent-{info_hash[0]}",
        total_size=size,
        now=NOW,
        storage_policy=policy,
        disk_snapshot=disk,
    )


@pytest.mark.asyncio
async def test_user_quota_rejects_only_new_logical_right(db_session: AsyncSession) -> None:
    user = await _user(db_session, "quota-user")
    policy = _policy(user=100)
    first = await _request(db_session, user, info_hash="a" * 40, size=60, policy=policy)
    replay = await _request(db_session, user, info_hash="a" * 40, size=60, policy=policy)

    with pytest.raises(StorageAdmissionError) as failure:
        await _request(db_session, user, info_hash="b" * 40, size=50, policy=policy)

    usage = await db_session.get(UserStorageUsage, user.id)
    assert first.request_created is True
    assert replay.request_created is False
    assert failure.value.code == "user_quota_exceeded"
    assert usage is not None and usage.logical_bytes == 60


@pytest.mark.asyncio
async def test_managed_quota_counts_shared_content_once(db_session: AsyncSession) -> None:
    first_user = await _user(db_session, "managed-first")
    second_user = await _user(db_session, "managed-second")
    policy = _policy(managed=100)
    await _request(db_session, first_user, info_hash="c" * 40, size=60, policy=policy)
    shared = await _request(db_session, second_user, info_hash="c" * 40, size=60, policy=policy)

    with pytest.raises(StorageAdmissionError) as failure:
        await _request(db_session, second_user, info_hash="d" * 40, size=50, policy=policy)

    ledger = await db_session.get(StorageLedger, 1)
    assert shared.managed_torrent_created is False
    assert failure.value.code == "managed_quota_exceeded"
    assert ledger is not None and ledger.managed_bytes == 60


@pytest.mark.asyncio
async def test_warning_allows_new_content_but_projected_critical_blocks_it(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, "pressure-user")
    policy = _policy()
    warning = await _request(
        db_session,
        user,
        info_hash="e" * 40,
        size=10,
        policy=policy,
        disk=StorageDiskSnapshot(1_000, 190),
    )

    with pytest.raises(StorageAdmissionError) as failure:
        await _request(
            db_session,
            user,
            info_hash="f" * 40,
            size=100,
            policy=policy,
            disk=StorageDiskSnapshot(1_000, 150),
        )

    assert warning.storage_pressure is StoragePressureState.WARNING
    assert failure.value.code == "disk_pressure_critical"
    assert failure.value.pressure is StoragePressureState.CRITICAL


@pytest.mark.asyncio
async def test_critical_disk_still_allows_a_shared_physical_torrent(
    db_session: AsyncSession,
) -> None:
    first_user = await _user(db_session, "critical-first")
    second_user = await _user(db_session, "critical-second")
    policy = _policy()
    await _request(db_session, first_user, info_hash="1" * 40, size=40, policy=policy)

    shared = await _request(
        db_session,
        second_user,
        info_hash="1" * 40,
        size=40,
        policy=policy,
        disk=StorageDiskSnapshot(1_000, 90),
    )

    assert shared.managed_torrent_created is False
    assert shared.storage_pressure is StoragePressureState.CRITICAL


@pytest.mark.asyncio
async def test_projected_free_reserve_blocks_new_content(db_session: AsyncSession) -> None:
    user = await _user(db_session, "reserve-user")
    with pytest.raises(StorageAdmissionError) as failure:
        await _request(
            db_session,
            user,
            info_hash="2" * 40,
            size=20,
            policy=_policy(minimum_free=90),
            disk=StorageDiskSnapshot(1_000, 100),
        )

    assert failure.value.code == "disk_pressure_critical"


@pytest.mark.asyncio
async def test_reconciler_repairs_counters_in_bounded_user_batches(
    db_session: AsyncSession,
) -> None:
    users = [await _user(db_session, f"reconcile-{index}") for index in range(3)]
    for index, user in enumerate(users):
        await _request(
            db_session,
            user,
            info_hash=str(index + 3) * 40,
            size=(index + 1) * 10,
            policy=_policy(),
        )
    ledger = await db_session.get(StorageLedger, 1)
    assert ledger is not None
    ledger.managed_bytes = 999
    for user in users:
        usage = await db_session.get(UserStorageUsage, user.id)
        assert usage is not None
        usage.logical_bytes = 999

    first = await reconcile_storage_counters(
        db_session,
        policy=_policy(),
        disk=StorageDiskSnapshot(1_000, 500),
        batch_size=2,
        now=NOW,
    )
    assert first.processed_users == 2
    assert first.completed is False
    assert first.next_user_id is not None
    second = await reconcile_storage_counters(
        db_session,
        policy=_policy(),
        disk=StorageDiskSnapshot(1_000, 500),
        batch_size=2,
        after_user_id=first.next_user_id,
        now=NOW,
    )

    assert second.processed_users == 1
    assert second.completed is True
    assert second.managed_bytes == 60
    for index, user in enumerate(users):
        usage = await db_session.get(UserStorageUsage, user.id)
        assert usage is not None and usage.logical_bytes == (index + 1) * 10


def test_policy_and_disk_snapshots_reject_invalid_values() -> None:
    with pytest.raises(ValueError):
        StorageDiskSnapshot(0, 0)
    with pytest.raises(ValueError):
        StorageAdmissionPolicy(0, 0, 0, 0, 90, 80)
    with pytest.raises(ValueError):
        StorageAdmissionPolicy.from_options({})
