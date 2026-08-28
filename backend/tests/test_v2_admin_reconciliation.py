import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin import reconcile_inventory
from app.auth.security import hash_password
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2Inventory,
    QBittorrentV2InventoryItem,
)
from app.models import ManagedTorrent, ManagedTorrentState, User
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
