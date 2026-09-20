import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.integrations.types import NewGreedyTorrent, QBittorrentTorrent
from app.main import app
from app.models import (
    DatabaseOptionAudit,
    ManagedTorrent,
    ManagedTorrentState,
    SchedulerState,
    StorageLedger,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    User,
    UserStorageUsage,
)
from app.options import PostgresOptionsRegistry


async def _admin(db: AsyncSession) -> User:
    user = User(
        username="central-admin",
        password_hash=hash_password("correct-horse-battery"),
        is_admin=True,
    )
    db.add(user)
    await PostgresOptionsRegistry().initialize(db)
    db.add_all(
        [
            SchedulerState(
                id=1,
                desired_generation=4,
                applied_generation=3,
                rounds=9,
                lease_owner="scheduler-test",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
            ),
            StorageLedger(
                id=1,
                managed_bytes=100,
                disk_total_bytes=1000,
                disk_free_bytes=600,
            ),
            UserStorageUsage(user_id=user.id, logical_bytes=150),
        ]
    )
    await db.commit()
    return user


async def _login(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "central-admin", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    token = client.cookies.get("wos_csrf")
    assert token is not None
    return {"X-CSRF-Token": token}


@pytest.mark.asyncio
async def test_admin_overview_exposes_options_scheduler_storage_and_bounded_audit(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _admin(db_session)
    await _login(client)

    response = await client.get("/api/v2/admin/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["scheduler"] == {
        "desired_generation": 4,
        "applied_generation": 3,
        "synchronized": False,
        "rounds": 9,
        "lease_active": True,
    }
    assert body["storage"]["managed_bytes"] == 100
    assert body["storage"]["logical_bytes"] == 150
    assert 0 < len(body["audit"]) <= 50
    c411 = next(section for section in body["sections"] if section["id"] == "c411_accounts")
    assert len(c411["fields"]) == 48
    assert sum(field["input_type"] == "secret" for field in c411["fields"]) == 16
    username = next(
        field for field in c411["fields"] if field["key"] == "WOS_C411_ACCOUNT_01_USERNAME"
    )
    assert username["input_type"] == "text"


@pytest.mark.asyncio
async def test_admin_option_update_requires_csrf_and_records_actor(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    admin = await _admin(db_session)
    headers = await _login(client)
    payload = {"changes": {"WOS_STORAGE_USER_MAX_BYTES": 1024}}

    rejected = await client.patch("/api/v2/admin/options", json=payload)
    accepted = await client.patch("/api/v2/admin/options", json=payload, headers=headers)

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["changed_keys"] == ["WOS_STORAGE_USER_MAX_BYTES"]
    assert accepted.json()["storage"]["user_quota_bytes"] == 1024
    audit = await db_session.scalar(
        select(DatabaseOptionAudit)
        .where(DatabaseOptionAudit.option_key == "WOS_STORAGE_USER_MAX_BYTES")
        .order_by(DatabaseOptionAudit.version.desc())
    )
    assert audit is not None
    assert audit.actor_user_id == admin.id


@pytest.mark.asyncio
async def test_admin_can_configure_c411_pair_without_exposing_passkey_in_audit(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _admin(db_session)
    headers = await _login(client)

    response = await client.patch(
        "/api/v2/admin/options",
        json={
            "changes": {
                "WOS_C411_ACCOUNT_01_USERNAME": "Thomas",
                "WOS_C411_ACCOUNT_01_NUMBER": "1001",
                "WOS_C411_ACCOUNT_01_PASSKEY": "admin-passkey-123",
            }
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    c411 = next(section for section in body["sections"] if section["id"] == "c411_accounts")
    fields = {field["key"]: field for field in c411["fields"]}
    assert fields["WOS_C411_ACCOUNT_01_USERNAME"]["value"] == "Thomas"
    assert fields["WOS_C411_ACCOUNT_01_NUMBER"]["value"] == "1001"
    assert fields["WOS_C411_ACCOUNT_01_PASSKEY"]["value"] == "admin-passkey-123"
    assert all("admin-passkey-123" not in repr(event) for event in body["audit"])


class _RuntimeMonitor:
    def __init__(self) -> None:
        self.resumed: list[dict[str, object]] = []

    async def qbittorrent_torrents(
        self,
    ) -> tuple[datetime, list[QBittorrentTorrent], bool]:
        return (
            datetime(2026, 1, 1, tzinfo=UTC),
            [
                QBittorrentTorrent(
                    id="a" * 40,
                    name="Linux distribution",
                    state="downloading",
                    progress=0.5,
                    size_bytes=1_000,
                    downloaded_bytes=500,
                    uploaded_bytes=25,
                    download_speed_bytes=120,
                    upload_speed_bytes=30,
                    ratio=0.05,
                    eta_seconds=60,
                    category=None,
                    tracker_host=None,
                )
            ],
            False,
        )

    async def newgreedy_torrents(
        self,
    ) -> tuple[datetime, list[tuple[NewGreedyTorrent, str | None]]]:
        return (
            datetime(2026, 1, 1, tzinfo=UTC),
            [
                (
                    NewGreedyTorrent(
                        id="a" * 40,
                        mode="seed",
                        downloaded_bytes=1_000,
                        reported_uploaded_bytes=2_000,
                        fake_uploaded_bytes=1_500,
                        ratio=2.0,
                        announce_count=3,
                        stalled=False,
                        target_reached=True,
                        last_announce_at=None,
                    ),
                    "Linux distribution",
                )
            ],
        )

    async def resume_managed_torrent(self, **values: object) -> None:
        self.resumed.append(values)


@pytest.mark.asyncio
async def test_admin_runtime_views_are_read_only_and_aggregate_service_data(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _admin(db_session)
    await _login(client)
    monkeypatch.setattr(app.state, "admin_runtime_monitor", _RuntimeMonitor())

    qb_response = await client.get("/api/v2/admin/runtime/qbittorrent")
    ng_response = await client.get("/api/v2/admin/runtime/newgreedy")

    assert qb_response.status_code == 200
    assert qb_response.json()["download_speed_bytes"] == 120
    assert qb_response.json()["upload_speed_bytes"] == 30
    assert qb_response.json()["torrents"][0]["name"] == "Linux distribution"
    assert ng_response.status_code == 200
    assert ng_response.json()["torrents"][0] == {
        "hash": "a" * 40,
        "name": "Linux distribution",
        "status": "target_reached",
        "downloaded_bytes": 1_000,
        "uploaded_bytes": 2_000,
        "ratio": 2.0,
    }


@pytest.mark.asyncio
async def test_admin_can_resume_owned_paused_torrent_with_durable_override(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _admin(db_session)
    headers = await _login(client)
    qb_account_ref = uuid.uuid4()
    torrent = ManagedTorrent(
        info_hash="a" * 40,
        name="Retained partial",
        total_size=1_000,
        state=ManagedTorrentState.PAUSED,
        progress=0.5,
        qb_state="stoppedDL",
        qbittorrent_account_ref=qb_account_ref,
    )
    db_session.add(torrent)
    await db_session.commit()
    monitor = _RuntimeMonitor()
    monkeypatch.setattr(app.state, "admin_runtime_monitor", monitor)

    rejected = await client.post(
        f"/api/v2/admin/runtime/qbittorrent/{torrent.id}/action",
        json={"action": "resume"},
    )
    response = await client.post(
        f"/api/v2/admin/runtime/qbittorrent/{torrent.id}/action",
        json={"action": "resume"},
        headers=headers,
    )

    assert rejected.status_code == 403
    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    await db_session.refresh(torrent)
    assert torrent.admin_forced_active is True
    assert torrent.purge_after is not None
    purge = await db_session.scalar(
        select(TorrentJob).where(
            TorrentJob.managed_torrent_id == torrent.id,
            TorrentJob.job_type == "PURGE_TORRENT",
            TorrentJob.state == TorrentJobState.QUEUED,
        )
    )
    assert purge is not None and purge.available_at == torrent.purge_after
    assert monitor.resumed[0]["info_hash"] == torrent.info_hash


@pytest.mark.asyncio
async def test_admin_qb_delete_cancels_request_and_schedules_full_purge(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = await _admin(db_session)
    headers = await _login(client)
    torrent = ManagedTorrent(
        info_hash="b" * 40,
        name="Large partial",
        total_size=2_000,
        state=ManagedTorrentState.PAUSED,
        progress=0.25,
        qb_state="stoppedDL",
        admin_forced_active=True,
    )
    request = TorrentRequest(
        managed_torrent=torrent,
        user_id=user.id,
        state=TorrentRequestState.ACTIVE,
    )
    db_session.add_all([torrent, request])
    await db_session.commit()
    monkeypatch.setattr(app.state, "admin_runtime_monitor", _RuntimeMonitor())

    response = await client.post(
        f"/api/v2/admin/runtime/qbittorrent/{torrent.id}/action",
        json={"action": "delete"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "torrent_id": str(torrent.id),
        "action": "delete",
        "status": "scheduled",
    }
    await db_session.refresh(torrent)
    await db_session.refresh(request)
    assert torrent.admin_forced_active is False
    assert torrent.purge_after is not None
    assert request.state is TorrentRequestState.CANCELLED


@pytest.mark.asyncio
async def test_admin_cleanup_lists_downloads_and_purges_only_explicit_filtered_ids(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    admin = await _admin(db_session)
    now = datetime.now(UTC).replace(microsecond=0)
    subscribed = ManagedTorrent(
        info_hash="b" * 40,
        name="Subscribed content",
        total_size=100,
        state=ManagedTorrentState.READY,
        progress=1,
        ready_at=now,
    )
    orphan = ManagedTorrent(
        info_hash="c" * 40,
        name="Orphan content",
        total_size=200,
        state=ManagedTorrentState.READY,
        progress=1,
        ready_at=now,
        purge_after=now + timedelta(hours=4),
    )
    downloading = ManagedTorrent(
        info_hash="d" * 40,
        name="Still downloading",
        total_size=300,
        state=ManagedTorrentState.DOWNLOADING,
        progress=0.5,
    )
    db_session.add_all([subscribed, orphan, downloading])
    await db_session.flush()
    subscription = TorrentRequest(
        user_id=admin.id,
        managed_torrent_id=subscribed.id,
        state=TorrentRequestState.READY,
        ready_at=now,
        unsubscribe_at=now + timedelta(hours=10),
    )
    db_session.add(subscription)
    usage = await db_session.get(UserStorageUsage, admin.id)
    assert usage is not None
    usage.logical_bytes = 1_000
    await db_session.commit()

    anonymous = await client.get("/api/v2/admin/cleanup")
    assert anonymous.status_code == 401
    headers = await _login(client)

    listing = await client.get("/api/v2/admin/cleanup")

    assert listing.status_code == 200
    items = {item["id"]: item for item in listing.json()["items"]}
    assert set(items) == {str(subscribed.id), str(orphan.id)}
    assert items[str(subscribed.id)]["subscriber_count"] == 1
    assert items[str(subscribed.id)]["size_bytes"] == 100
    expected_deletion = now + timedelta(hours=58)
    assert datetime.fromisoformat(items[str(subscribed.id)]["deletion_at"]) == expected_deletion
    assert items[str(orphan.id)]["subscriber_count"] == 0
    assert datetime.fromisoformat(items[str(orphan.id)]["deletion_at"]) == now + timedelta(hours=4)

    rejected = await client.post(
        "/api/v2/admin/cleanup/purge",
        json={"torrent_ids": [str(orphan.id)]},
    )
    assert rejected.status_code == 403

    filtered_purge = await client.post(
        "/api/v2/admin/cleanup/purge",
        json={"torrent_ids": [str(orphan.id)]},
        headers=headers,
    )

    assert filtered_purge.status_code == 200
    assert filtered_purge.json() == {
        "requested": 1,
        "scheduled": 1,
        "scheduled_ids": [str(orphan.id)],
        "skipped_ids": [],
    }
    await db_session.refresh(orphan)
    await db_session.refresh(subscribed)
    await db_session.refresh(subscription)
    assert orphan.purge_after is not None
    assert subscribed.purge_after is None
    assert subscription.state is TorrentRequestState.READY
    orphan_job = await db_session.scalar(
        select(TorrentJob).where(
            TorrentJob.managed_torrent_id == orphan.id,
            TorrentJob.job_type == "PURGE_TORRENT",
        )
    )
    assert orphan_job is not None
    assert orphan_job.state is TorrentJobState.QUEUED

    individual_purge = await client.post(
        "/api/v2/admin/cleanup/purge",
        json={"torrent_ids": [str(subscribed.id)]},
        headers=headers,
    )

    assert individual_purge.status_code == 200
    reloaded_subscription = await db_session.get(TorrentRequest, subscription.id)
    await db_session.refresh(usage)
    assert reloaded_subscription is not None
    assert reloaded_subscription.state is TorrentRequestState.CANCELLED
    assert usage.logical_bytes == 900
