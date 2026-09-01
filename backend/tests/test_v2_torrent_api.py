import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.coordination import TorrentEventType, TorrentRealtimeEvent
from app.main import app
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.options import PostgresOptionsRegistry


class RecordingRedis:
    def __init__(self) -> None:
        self.events: list[tuple[uuid.UUID, TorrentRealtimeEvent]] = []
        self.queue_events: list[datetime] = []
        self.signals = 0

    async def signal_job_available(self) -> bool:
        self.signals += 1
        return True

    async def publish_torrent_event(
        self,
        user_id: uuid.UUID,
        event: TorrentRealtimeEvent,
    ) -> bool:
        self.events.append((user_id, event))
        return True

    async def publish_torrent_queue_changed(self, occurred_at: datetime) -> bool:
        self.queue_events.append(occurred_at)
        return True


def _encode(value: object) -> bytes:
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, list):
        return b"l" + b"".join(_encode(item) for item in value) + b"e"
    assert isinstance(value, dict)
    return b"d" + b"".join(_encode(key) + _encode(value[key]) for key in sorted(value)) + b"e"


def torrent_content(name: bytes = b"Film.mkv", size: int = 5) -> bytes:
    return _encode(
        {
            b"announce": b"https://c411.org/announce/old-user-passkey",
            b"info": {
                b"length": size,
                b"name": name,
                b"piece length": 16_384,
                b"pieces": b"p" * 20,
            },
        }
    )


async def prepare_user(db: AsyncSession, username: str = "thomas") -> User:
    user = User(username=username, password_hash=hash_password("correct-horse-battery"))
    db.add(user)
    await PostgresOptionsRegistry().initialize(db)
    await db.commit()
    return user


async def login(client: AsyncClient, username: str = "thomas") -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    token = client.cookies.get("wos_csrf")
    assert token is not None
    return {"X-CSRF-Token": token}


@pytest.mark.asyncio
async def test_v2_upload_is_durable_idempotent_and_secret_free(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    user = await prepare_user(db_session)
    headers = await login(client)
    redis = RecordingRedis()
    app.state.redis_coordinator = redis

    first = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )
    second = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )

    assert first.status_code == 201, first.text
    assert first.json()["created"] is True
    assert first.json()["state"] == "requested"
    assert second.status_code == 201, second.text
    assert second.json()["created"] is False
    assert second.json()["id"] == first.json()["id"]
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(TorrentRequest)) == 1
    assert await db_session.scalar(select(func.count()).select_from(TorrentJob)) == 1
    managed = await db_session.scalar(select(ManagedTorrent))
    assert managed is not None
    staged = data_root / "control" / "torrent-input" / f"{managed.storage_key.hex}.torrent"
    assert staged.is_file()
    assert b"old-user-passkey" not in staged.read_bytes()
    assert user.id == (await db_session.scalar(select(TorrentRequest.user_id)))
    assert redis.signals == 1
    assert [(owner, event.event_type) for owner, event in redis.events] == [
        (user.id, TorrentEventType.REQUESTED)
    ]
    assert redis.events[0][1].request_id == uuid.UUID(first.json()["id"])


@pytest.mark.asyncio
async def test_v2_listing_is_owned_paginated_and_database_only(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    other = User(username="alice", password_hash=hash_password("correct-horse-battery"))
    db_session.add(other)
    await db_session.commit()
    first_torrent = ManagedTorrent(info_hash="a" * 40, name="Owned", total_size=100)
    other_torrent = ManagedTorrent(info_hash="b" * 40, name="Hidden", total_size=200)
    first_torrent.state = ManagedTorrentState.DOWNLOADING
    first_torrent.progress = 0.42
    db_session.add_all(
        [
            first_torrent,
            other_torrent,
            TorrentRequest(user_id=owner.id, managed_torrent=first_torrent),
            TorrentRequest(user_id=other.id, managed_torrent=other_torrent),
        ]
    )
    await db_session.commit()
    await login(client)

    response = await client.get("/api/v2/torrents?offset=0&limit=1")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["offset"] == 0
    assert response.json()["limit"] == 1
    assert [item["name"] for item in response.json()["items"]] == ["Owned"]
    assert response.json()["items"][0]["progress"] == pytest.approx(0.42)
    assert response.json()["items"][0]["queue_status"] == "waiting"
    assert response.json()["items"][0]["queue_position_estimate"] == 1
    assert response.json()["items"][0]["queue_total_estimate"] == 1


@pytest.mark.asyncio
async def test_shared_torrent_exposes_one_physical_estimate_without_owner_data(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    first_owner = await prepare_user(db_session)
    second_owner = User(
        username="alice",
        password_hash=hash_password("correct-horse-battery"),
    )
    shared = ManagedTorrent(
        info_hash="7" * 40,
        name="Shared queue",
        total_size=100,
        state=ManagedTorrentState.PAUSED,
    )
    db_session.add_all(
        [
            second_owner,
            shared,
            TorrentRequest(user=first_owner, managed_torrent=shared),
            TorrentRequest(user=second_owner, managed_torrent=shared),
        ]
    )
    await db_session.commit()

    await login(client, first_owner.username)
    first = (await client.get("/api/v2/torrents")).json()["items"][0]
    await login(client, second_owner.username)
    second = (await client.get("/api/v2/torrents")).json()["items"][0]

    assert first["queue_position_estimate"] == second["queue_position_estimate"] == 1
    assert first["queue_total_estimate"] == second["queue_total_estimate"] == 1
    assert first["queue_status"] == second["queue_status"] == "waiting"
    forbidden = {
        "info_hash",
        "storage_key",
        "tracker_account_ref",
        "qbittorrent_account_ref",
        "passkey",
        "user_id",
    }
    assert forbidden.isdisjoint(first)
    assert forbidden.isdisjoint(second)


@pytest.mark.asyncio
async def test_v2_api_requires_authentication_csrf_and_valid_torrent(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await prepare_user(db_session)
    unauthenticated = await client.get("/api/v2/torrents")
    headers = await login(client)
    no_csrf = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
    )
    invalid = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("bad.torrent", b"invalid", "application/x-bittorrent")},
        headers=headers,
    )
    wrong_name = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("bad.txt", torrent_content(), "text/plain")},
        headers=headers,
    )

    assert unauthenticated.status_code == 401
    assert no_csrf.status_code == 403
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "torrent_invalid"
    assert wrong_name.status_code == 422
    assert wrong_name.json()["detail"]["code"] == "torrent_filename_invalid"


@pytest.mark.asyncio
async def test_v2_listing_exposes_only_bounded_error_code(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    managed = ManagedTorrent(
        info_hash="c" * 40,
        name="Failed",
        total_size=100,
        state=ManagedTorrentState.ERROR,
    )
    request = TorrentRequest(user_id=owner.id, managed_torrent=managed)
    db_session.add_all([managed, request])
    await db_session.commit()
    await login(client)

    response = await client.get("/api/v2/torrents")

    assert response.status_code == 200
    assert response.json()["items"][0]["state"] == "error"
    assert response.json()["items"][0]["error_code"] == "torrent_failed"
    assert "info_hash" not in response.json()["items"][0]
    assert "storage" not in response.text.lower()


@pytest.mark.asyncio
async def test_ready_shared_deadline_is_authoritative_secret_free_and_realtime_extended(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    headers = await login(client)
    redis = RecordingRedis()
    app.state.redis_coordinator = redis
    first = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )
    assert first.status_code == 201

    managed = await db_session.scalar(select(ManagedTorrent))
    first_request = await db_session.scalar(select(TorrentRequest))
    assert managed is not None and first_request is not None
    ready_at = datetime.now(UTC)
    initial_deadline = ready_at + timedelta(days=5)
    managed.state = ManagedTorrentState.READY
    managed.progress = 1
    managed.ready_at = ready_at
    managed.retention_expires_at = initial_deadline
    first_request.state = TorrentRequestState.READY
    first_request.ready_at = ready_at
    second_owner = User(
        username="alice",
        password_hash=hash_password("correct-horse-battery"),
    )
    db_session.add(second_owner)
    await db_session.flush()
    owner_id = owner.id
    owner_username = owner.username
    second_owner_id = second_owner.id
    await db_session.commit()
    redis.events.clear()

    second_headers = await login(client, "alice")
    second = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=second_headers,
    )
    assert second.status_code == 201, second.text
    extended_deadline = ready_at + timedelta(days=6)
    assert datetime.fromisoformat(second.json()["retention_expires_at"]) == extended_deadline

    retention_events = [
        (user_id, event.request_id)
        for user_id, event in redis.events
        if event.event_type is TorrentEventType.RETENTION_EXTENDED
    ]
    assert {user_id for user_id, _ in retention_events} == {owner_id, second_owner_id}
    assert len(retention_events) == 2
    assert all(
        set(event.payload()) == {"type", "request_id", "occurred_at"}
        for _, event in redis.events
        if event.event_type is TorrentEventType.RETENTION_EXTENDED
    )

    db_session.expire_all()
    second_listing = await client.get("/api/v2/torrents")
    assert second_listing.status_code == 200
    second_item = second_listing.json()["items"][0]
    assert datetime.fromisoformat(second_item["retention_expires_at"]) == extended_deadline

    await login(client, owner_username)
    first_listing = await client.get("/api/v2/torrents")
    assert first_listing.status_code == 200
    first_item = first_listing.json()["items"][0]
    assert first_item["retention_expires_at"] == second_item["retention_expires_at"]
    forbidden = {
        "info_hash",
        "storage_key",
        "tracker_account_ref",
        "qbittorrent_account_ref",
        "passkey",
        "path",
    }
    assert forbidden.isdisjoint(first_item)


@pytest.mark.asyncio
async def test_expired_request_never_exposes_a_stale_countdown(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    managed = ManagedTorrent(
        info_hash="9" * 40,
        name="Expired",
        total_size=100,
        state=ManagedTorrentState.READY,
        progress=1,
        ready_at=expired_at - timedelta(days=5),
        retention_expires_at=expired_at,
    )
    request = TorrentRequest(
        user_id=owner.id,
        managed_torrent=managed,
        state=TorrentRequestState.EXPIRED,
    )
    db_session.add(request)
    await db_session.commit()
    await login(client)

    response = await client.get("/api/v2/torrents")

    assert response.status_code == 200
    assert response.json()["items"][0]["state"] == "expired"
    assert response.json()["items"][0]["retention_expires_at"] is None


@pytest.mark.asyncio
async def test_v2_cancellation_schedules_retained_purge_and_is_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    managed = ManagedTorrent(
        info_hash="d" * 40,
        name="Cancelled",
        total_size=100,
        state=ManagedTorrentState.READY,
        progress=1,
    )
    request = TorrentRequest(
        user_id=owner.id,
        managed_torrent=managed,
        state=TorrentRequestState.READY,
    )
    db_session.add(request)
    await db_session.commit()
    request_id = request.id
    headers = await login(client)
    redis = RecordingRedis()
    app.state.redis_coordinator = redis

    first = await client.delete(f"/api/v2/torrents/{request_id}", headers=headers)
    second = await client.delete(f"/api/v2/torrents/{request_id}", headers=headers)

    assert first.status_code == second.status_code == 204, (first.text, second.text)
    assert [(user_id, event.event_type, event.request_id) for user_id, event in redis.events] == [
        (owner.id, TorrentEventType.CANCELLED, request_id)
    ]
    await db_session.refresh(request)
    await db_session.refresh(managed)
    assert request.state is TorrentRequestState.CANCELLED
    assert managed.state is ManagedTorrentState.PURGE_PENDING
    assert managed.purge_after is not None
    jobs = list(
        (
            await db_session.scalars(
                select(TorrentJob).where(TorrentJob.job_type == "PURGE_TORRENT")
            )
        ).all()
    )
    assert len(jobs) == 1
    assert jobs[0].state is TorrentJobState.QUEUED


@pytest.mark.asyncio
async def test_only_last_shared_waiting_owner_cancellation_invalidates_queue_once(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    first_owner = await prepare_user(db_session)
    second_owner = User(
        username="alice",
        password_hash=hash_password("correct-horse-battery"),
    )
    managed = ManagedTorrent(
        info_hash="e" * 40,
        name="Shared waiting",
        total_size=100,
        state=ManagedTorrentState.PAUSED,
        desired_active=False,
    )
    first_request = TorrentRequest(
        user_id=first_owner.id,
        managed_torrent=managed,
        state=TorrentRequestState.ACTIVE,
    )
    second_request = TorrentRequest(
        user=second_owner,
        managed_torrent=managed,
        state=TorrentRequestState.ACTIVE,
    )
    db_session.add_all((first_request, second_request))
    await db_session.commit()
    first_request_id = first_request.id
    second_request_id = second_request.id
    redis = RecordingRedis()
    app.state.redis_coordinator = redis

    first_headers = await login(client)
    first = await client.delete(
        f"/api/v2/torrents/{first_request_id}",
        headers=first_headers,
    )
    second_headers = await login(client, "alice")
    second = await client.delete(
        f"/api/v2/torrents/{second_request_id}",
        headers=second_headers,
    )

    assert first.status_code == second.status_code == 204
    assert len(redis.queue_events) == 1
    payload = TorrentRealtimeEvent(
        TorrentEventType.QUEUE_CHANGED,
        None,
        redis.queue_events[0],
    ).payload()
    assert set(payload) == {"type", "occurred_at"}


@pytest.mark.asyncio
async def test_v2_cancellation_cannot_cross_owner_boundary(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    other = User(username="other-owner", password_hash=hash_password("correct-horse-battery"))
    managed = ManagedTorrent(info_hash="e" * 40, name="Private", total_size=1)
    request = TorrentRequest(user=other, managed_torrent=managed)
    db_session.add_all([other, request])
    await db_session.commit()
    headers = await login(client, owner.username)

    response = await client.delete(f"/api/v2/torrents/{request.id}", headers=headers)

    assert response.status_code == 404
