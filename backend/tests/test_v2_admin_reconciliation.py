import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import (
    ReconciliationRecoveryError,
    reconcile_inventory,
    recover_orphaned_torrent,
    recovery_snapshot,
)
from app.auth.security import hash_password
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2Inventory,
    QBittorrentV2InventoryItem,
)
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    StorageLedger,
    TorrentFile,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    User,
    UserStorageUsage,
)
from app.storage.shared import SharedContentInventory, SharedContentStore, SharedContentStoreError


def _torrent(*, info_hash: str, storage_key: uuid.UUID) -> ManagedTorrent:
    return ManagedTorrent(
        id=uuid.uuid4(),
        info_hash=info_hash,
        storage_key=storage_key,
        name="Managed",
        total_size=1,
        state=ManagedTorrentState.READY,
    )


def test_reconciliation_reports_actionable_drift_and_keeps_external_qb_read_only() -> None:
    storage_key = uuid.uuid4()
    torrent = _torrent(info_hash="a" * 40, storage_key=storage_key)
    external = QBittorrentV2InventoryItem("b" * 40, None, False)
    mismatched = QBittorrentV2InventoryItem("a" * 40, uuid.uuid4(), True)

    report = reconcile_inventory(
        (torrent,),
        database_truncated=False,
        qbittorrent=QBittorrentV2Inventory((mismatched, external), False),
        storage=SharedContentInventory((storage_key,), 0, False),
    )

    assert report.external_torrents == 1
    assert {(item.code, item.action) for item in report.anomalies} == {
        ("qb_identity_mismatch", "manual_review"),
        ("external_torrents_read_only", "none"),
    }


def test_shared_storage_inventory_is_bounded_and_does_not_follow_symlinks(
    tmp_path: Path,
) -> None:
    content = tmp_path / "content"
    content.mkdir()
    first = uuid.uuid4()
    second = uuid.uuid4()
    (content / first.hex).mkdir()
    (content / second.hex).mkdir()
    (content / "unsafe").symlink_to(tmp_path)

    inventory = SharedContentStore(tmp_path).inventory(limit=1)

    assert len(inventory.keys) == 1
    assert inventory.truncated is True


def test_shared_storage_exact_presence_fails_closed_on_symlink(tmp_path: Path) -> None:
    content = tmp_path / "content"
    content.mkdir()
    present = uuid.uuid4()
    missing = uuid.uuid4()
    unsafe = uuid.uuid4()
    (content / present.hex).mkdir()
    (content / unsafe.hex).symlink_to(tmp_path)
    store = SharedContentStore(tmp_path)

    assert store.contains(present) is True
    assert store.contains(missing) is False
    with pytest.raises(SharedContentStoreError):
        store.contains(unsafe)
    empty_root = tmp_path / "empty-root"
    empty_root.mkdir()
    assert SharedContentStore(empty_root).contains(missing) is False


def test_reconciliation_selects_metadata_purge_only_when_both_physical_sides_are_absent() -> None:
    torrent = _torrent(info_hash="f" * 40, storage_key=uuid.uuid4())

    report = reconcile_inventory(
        (torrent,),
        database_truncated=False,
        qbittorrent=QBittorrentV2Inventory((), False),
        storage=SharedContentInventory((), 0, False),
    )

    assert {(item.code, item.action) for item in report.anomalies} == {
        ("missing_qb_torrent", "purge_metadata"),
        ("missing_storage", "purge_metadata"),
    }


@pytest.mark.asyncio
async def test_recovery_cancels_orphan_rights_without_deleting_metadata(
    db_session: AsyncSession,
) -> None:
    user = User(username="orphan-owner", password_hash="hash")
    torrent = _torrent(info_hash="c" * 40, storage_key=uuid.uuid4())
    torrent.state = ManagedTorrentState.READY
    request = TorrentRequest(user=user, managed_torrent=torrent, state=TorrentRequestState.READY)
    usage = UserStorageUsage(user=user, logical_bytes=1)
    job = TorrentJob(
        managed_torrent=torrent,
        torrent_request=request,
        job_type="SYNC_TORRENT",
        idempotency_key=f"sync:{torrent.id}",
        state=TorrentJobState.QUEUED,
        max_attempts=3,
    )
    db_session.add_all([request, usage, job, StorageLedger(id=1, managed_bytes=1)])
    await db_session.commit()
    expected = await recovery_snapshot(db_session, torrent.id)

    result = await recover_orphaned_torrent(
        db_session,
        torrent.id,
        action="cancel_requests",
        qbittorrent_present=False,
        storage_present=True,
        expected=expected,
    )
    await db_session.commit()

    assert result.cancelled_requests == 1
    assert result.metadata_purged is False
    assert torrent.state is ManagedTorrentState.ERROR
    assert request.state is TorrentRequestState.CANCELLED
    assert usage.logical_bytes == 0
    assert job.state is TorrentJobState.CANCELLED
    ledger = await db_session.get(StorageLedger, 1)
    assert ledger is not None and ledger.managed_bytes == 1


@pytest.mark.asyncio
async def test_recovery_purges_metadata_only_after_both_physical_checks_are_absent(
    db_session: AsyncSession,
) -> None:
    torrent = _torrent(info_hash="d" * 40, storage_key=uuid.uuid4())
    file = TorrentFile(
        managed_torrent=torrent,
        file_index=0,
        relative_path="file.bin",
        size=1,
    )
    db_session.add_all([file, StorageLedger(id=1, managed_bytes=1)])
    await db_session.commit()
    torrent_id = torrent.id
    expected = await recovery_snapshot(db_session, torrent_id)

    with pytest.raises(ReconciliationRecoveryError, match="physical_state_requires_manual_review"):
        await recover_orphaned_torrent(
            db_session,
            torrent_id,
            action="purge_metadata",
            qbittorrent_present=False,
            storage_present=True,
            expected=expected,
        )
    await db_session.rollback()
    result = await recover_orphaned_torrent(
        db_session,
        torrent_id,
        action="purge_metadata",
        qbittorrent_present=False,
        storage_present=False,
        expected=expected,
    )
    await db_session.commit()

    assert result.metadata_purged is True
    stored = await db_session.get(ManagedTorrent, torrent_id)
    assert stored is not None and stored.state is ManagedTorrentState.PURGED
    ledger = await db_session.get(StorageLedger, 1)
    assert ledger is not None and ledger.managed_bytes == 0


@pytest.mark.asyncio
async def test_metadata_recovery_rejects_stale_request_snapshot(
    db_session: AsyncSession,
) -> None:
    first = User(username="first-orphan-owner", password_hash="hash")
    torrent = _torrent(info_hash="e" * 40, storage_key=uuid.uuid4())
    original = TorrentRequest(
        user=first,
        managed_torrent=torrent,
        state=TorrentRequestState.READY,
    )
    db_session.add(original)
    await db_session.commit()
    expected = await recovery_snapshot(db_session, torrent.id)
    second = User(username="late-orphan-owner", password_hash="hash")
    late = TorrentRequest(
        user=second,
        managed_torrent=torrent,
        state=TorrentRequestState.ACTIVE,
    )
    db_session.add(late)
    await db_session.commit()
    original_id = original.id
    late_id = late.id

    with pytest.raises(ReconciliationRecoveryError, match="recovery_state_changed"):
        await recover_orphaned_torrent(
            db_session,
            torrent.id,
            action="purge_metadata",
            qbittorrent_present=False,
            storage_present=False,
            expected=expected,
        )

    await db_session.rollback()
    stored_original = await db_session.get(TorrentRequest, original_id)
    stored_late = await db_session.get(TorrentRequest, late_id)
    assert stored_original is not None and stored_original.state is TorrentRequestState.READY
    assert stored_late is not None and stored_late.state is TorrentRequestState.ACTIVE


@pytest.mark.asyncio
async def test_metadata_recovery_refuses_any_active_worker_job(
    db_session: AsyncSession,
) -> None:
    torrent = _torrent(info_hash="9" * 40, storage_key=uuid.uuid4())
    job = TorrentJob(
        managed_torrent=torrent,
        job_type="ADD_TORRENT",
        idempotency_key=f"add:{torrent.id}",
        state=TorrentJobState.QUEUED,
        max_attempts=3,
    )
    db_session.add(job)
    await db_session.commit()
    expected = await recovery_snapshot(db_session, torrent.id)

    with pytest.raises(ReconciliationRecoveryError, match="recovery_jobs_active"):
        await recover_orphaned_torrent(
            db_session,
            torrent.id,
            action="purge_metadata",
            qbittorrent_present=False,
            storage_present=False,
            expected=expected,
        )


@pytest.mark.asyncio
async def test_admin_reconciliation_is_admin_only_and_degrades_when_services_are_absent(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = User(
        username="reconcile-admin",
        password_hash=hash_password("correct-horse-battery"),
        is_admin=True,
    )
    db_session.add(admin)
    await db_session.commit()

    def unavailable_storage(_store: SharedContentStore, *, limit: int) -> SharedContentInventory:
        assert limit == 25
        assert db_session.in_transaction() is False
        raise SharedContentStoreError("storage unavailable")

    monkeypatch.setattr(SharedContentStore, "inventory", unavailable_storage)

    anonymous = await client.get("/api/v2/admin/reconciliation")
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "reconcile-admin", "password": "correct-horse-battery"},
    )
    response = await client.get("/api/v2/admin/reconciliation?limit=25")

    assert anonymous.status_code == 401
    assert login.status_code == 200
    assert response.status_code == 200
    assert response.json()["database_scanned"] == 0
    assert {item["code"] for item in response.json()["anomalies"]} == {
        "qbittorrent_unavailable",
        "storage_unavailable",
    }
