import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Never

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.coordination import TorrentEventType, TorrentRealtimeEvent
from app.main import app
from app.models import (
    DatabaseOption,
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


def game_torrent_content(*, unsafe_attribute: bytes | None = None) -> bytes:
    paths = [
        [b"game", b"setup.exe"],
        [b"game", b"bin", b"game.dll"],
        [b"game", b"data", b"content.pak"],
        [b"game", b"data", b"archive.bin"],
        [b"game", b"config", b"settings.ini"],
        [b"README"],
    ]
    files: list[dict[bytes, object]] = []
    for path in paths:
        entry: dict[bytes, object] = {b"length": 5, b"path": path}
        if unsafe_attribute is not None and path[-1] == b"setup.exe":
            entry[b"attr"] = unsafe_attribute
            if unsafe_attribute == b"l":
                entry[b"symlink path"] = [b"elsewhere"]
        files.append(entry)
    return _encode(
        {
            b"announce": b"https://c411.org/announce/old-user-passkey",
            b"info": {
                b"files": files,
                b"name": b"Game",
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
async def test_listing_sorts_before_pagination_and_filters_retention_facets(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    now = datetime.now(UTC)
    for index in range(30):
        managed = ManagedTorrent(
            info_hash=f"{index + 1:040x}",
            name=("avatar" if index == 0 else "Alien" if index == 1 else f"Film {29 - index:02d}"),
            total_size=index + 1,
            state=ManagedTorrentState.READY,
            progress=1,
            ready_at=now - timedelta(hours=20),
        )
        db_session.add(
            TorrentRequest(
                user_id=owner.id,
                managed_torrent=managed,
                state=TorrentRequestState.READY,
                ready_at=now - timedelta(hours=20),
                unsubscribe_at=now + timedelta(hours=80),
            )
        )
    orange = TorrentRequest(
        user_id=owner.id,
        managed_torrent=ManagedTorrent(
            info_hash="e" * 40,
            name="Orange",
            total_size=100,
            state=ManagedTorrentState.READY,
            progress=1,
        ),
        state=TorrentRequestState.READY,
        ready_at=now - timedelta(hours=50),
        unsubscribe_at=now + timedelta(hours=50),
    )
    red = TorrentRequest(
        user_id=owner.id,
        managed_torrent=ManagedTorrent(
            info_hash="f" * 40,
            name="Red",
            total_size=101,
            state=ManagedTorrentState.READY,
            progress=1,
        ),
        state=TorrentRequestState.READY,
        ready_at=now - timedelta(hours=90),
        unsubscribe_at=now + timedelta(hours=10),
    )
    db_session.add_all([orange, red])
    await db_session.commit()
    await login(client)

    first = await client.get(
        "/api/v2/torrents",
        params={"limit": 25, "sort_by": "name", "sort_order": "asc"},
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["total"] == 32
    assert len(payload["items"]) == 25
    assert [item["name"].casefold() for item in payload["items"]] == sorted(
        item["name"].casefold() for item in payload["items"]
    )
    second = await client.get(
        "/api/v2/torrents",
        params={"offset": 25, "limit": 25, "sort_by": "name", "sort_order": "asc"},
    )
    all_names = [item["name"] for item in payload["items"] + second.json()["items"]]
    assert [name.casefold() for name in all_names] == sorted(name.casefold() for name in all_names)
    assert payload["retention_counts"] == {"green": 30, "orange": 1, "red": 1}

    filtered = await client.get(
        "/api/v2/torrents",
        params={"retention_bucket": "red", "sort_by": "size", "sort_order": "desc"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["name"] == "Red"


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
async def test_v2_individual_and_batch_style_upload_accept_ordinary_game_files(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await prepare_user(db_session)
    headers = await login(client)
    app.state.redis_coordinator = RecordingRedis()

    # The frontend batch sends one request per .torrent through this same endpoint.
    for filename, content in (
        ("game.torrent", game_torrent_content()),
        ("readme.torrent", torrent_content(name=b"README")),
    ):
        response = await client.post(
            "/api/v2/torrents",
            files={"torrent": (filename, content, "application/x-bittorrent")},
            headers=headers,
        )
        assert response.status_code == 201, response.text
        assert response.json()["created"] is True

    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("attribute", [b"l", b"x", b"z"])
async def test_v2_upload_still_rejects_unsafe_file_attributes_regardless_of_extension(
    client: AsyncClient,
    db_session: AsyncSession,
    attribute: bytes,
) -> None:
    await prepare_user(db_session)
    headers = await login(client)

    response = await client.post(
        "/api/v2/torrents",
        files={
            "torrent": (
                "game.torrent",
                game_torrent_content(unsafe_attribute=attribute),
                "application/x-bittorrent",
            )
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "torrent_unsafe_file_attribute"
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0


@pytest.mark.asyncio
async def test_v2_upload_still_rejects_unsafe_exe_path(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await prepare_user(db_session)
    headers = await login(client)

    response = await client.post(
        "/api/v2/torrents",
        files={
            "torrent": (
                "game.torrent",
                torrent_content(name=b"../setup.exe"),
                "application/x-bittorrent",
            )
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "torrent_invalid"
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0


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
async def test_v2_listing_exposes_retry_time_only_during_cooldown(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    retry_at = datetime.now(UTC) + timedelta(hours=1)
    managed = ManagedTorrent(
        info_hash="8" * 40,
        name="Cooling down",
        total_size=100,
        state=ManagedTorrentState.PAUSED,
        scheduler_retry_at=retry_at,
    )
    db_session.add_all(
        [
            managed,
            TorrentRequest(user=owner, managed_torrent=managed),
        ]
    )
    await db_session.commit()
    await login(client)

    cooling = (await client.get("/api/v2/torrents")).json()["items"][0]
    assert cooling["queue_status"] == "cooldown"
    assert datetime.fromisoformat(cooling["scheduler_retry_at"]) == retry_at

    managed.scheduler_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    waiting = (await client.get("/api/v2/torrents")).json()["items"][0]
    assert waiting["queue_status"] == "waiting"
    assert waiting["scheduler_retry_at"] is None


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
async def test_v2_upload_body_is_not_parsed_before_authentication(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await prepare_user(db_session)

    async def forbidden(self: object) -> Never:
        raise AssertionError("an anonymous multipart body must not be parsed")

    monkeypatch.setattr("starlette.formparsers.MultiPartParser.parse", forbidden)

    anonymous = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
    )

    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_v2_upload_rejects_oversized_or_undeclared_bodies_before_parsing(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await prepare_user(db_session)
    headers = await login(client)

    async def forbidden(self: object) -> Never:
        raise AssertionError("an unbounded multipart body must not be parsed")

    monkeypatch.setattr("starlette.formparsers.MultiPartParser.parse", forbidden)

    oversized = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("big.torrent", b"0" * (5 * 1024 * 1024), "application/x-bittorrent")},
        headers=headers,
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield b"--x\r\n"

    undeclared = await client.post(
        "/api/v2/torrents",
        content=chunks(),
        headers={**headers, "Content-Type": "multipart/form-data; boundary=x"},
    )

    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] == "torrent_too_large"
    assert undeclared.status_code == 411
    assert undeclared.json()["detail"]["code"] == "torrent_length_required"


@pytest.mark.asyncio
async def test_v2_upload_without_a_torrent_part_is_a_validation_error(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await prepare_user(db_session)
    headers = await login(client)

    response = await client.post(
        "/api/v2/torrents",
        files={"other": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "torrent_missing"


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
    job = TorrentJob(
        managed_torrent=managed,
        torrent_request=request,
        job_type="ADD_TORRENT",
        idempotency_key="add:failed-safe-diagnostic",
        state=TorrentJobState.FAILED,
        last_error_code="torrent_unsafe_file_attribute",
        finished_at=datetime.now(UTC),
    )
    db_session.add_all([managed, request, job])
    await db_session.commit()
    await login(client)

    response = await client.get("/api/v2/torrents")

    assert response.status_code == 200
    assert response.json()["items"][0]["state"] == "error"
    assert response.json()["items"][0]["error_code"] == "torrent_unsafe_file_attribute"
    assert "info_hash" not in response.json()["items"][0]
    assert "storage" not in response.text.lower()


@pytest.mark.asyncio
async def test_failed_add_can_be_retried_without_reuploading_payload(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await prepare_user(db_session)
    headers = await login(client)
    redis = RecordingRedis()
    app.state.redis_coordinator = redis
    uploaded = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )
    assert uploaded.status_code == 201, uploaded.text

    managed = await db_session.scalar(select(ManagedTorrent))
    original_job = await db_session.scalar(select(TorrentJob))
    assert managed is not None and original_job is not None
    managed.state = ManagedTorrentState.ERROR
    original_job.state = TorrentJobState.FAILED
    original_job.last_error_code = "torrent_integration_unavailable"
    original_job.finished_at = datetime.now(UTC)
    await db_session.commit()
    generation = managed.lifecycle_generation
    redis.signals = 0
    redis.events.clear()

    retried = await client.post(
        f"/api/v2/torrents/{uploaded.json()['id']}/retry",
        headers=headers,
    )

    assert retried.status_code == 200, retried.text
    assert retried.json()["state"] == "requested"
    await db_session.refresh(managed)
    assert managed.state is ManagedTorrentState.PENDING
    assert managed.lifecycle_generation == generation + 1
    jobs = list(
        (
            await db_session.scalars(
                select(TorrentJob).order_by(TorrentJob.created_at.asc(), TorrentJob.id.asc())
            )
        ).all()
    )
    assert len(jobs) == 2
    assert jobs[-1].state is TorrentJobState.QUEUED
    assert jobs[-1].idempotency_key == f"add:{managed.id}:retry:{generation + 1}"
    assert redis.signals == 1
    assert [event.event_type for _, event in redis.events] == [TorrentEventType.REQUESTED]

    duplicate = await client.post(
        f"/api/v2/torrents/{uploaded.json()['id']}/retry",
        headers=headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "torrent_retry_not_allowed"


@pytest.mark.asyncio
async def test_ready_deadline_is_per_subscription_and_secret_free(
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
    initial_deadline = ready_at + timedelta(hours=12)
    managed.state = ManagedTorrentState.READY
    managed.progress = 1
    managed.ready_at = ready_at
    first_request.state = TorrentRequestState.READY
    first_request.ready_at = ready_at
    first_request.unsubscribe_at = initial_deadline
    second_owner = User(
        username="alice",
        password_hash=hash_password("correct-horse-battery"),
    )
    db_session.add(second_owner)
    await db_session.flush()
    owner_username = owner.username
    await db_session.commit()
    redis.events.clear()

    second_headers = await login(client, "alice")
    second = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=second_headers,
    )
    assert second.status_code == 201, second.text
    second_deadline = datetime.fromisoformat(second.json()["unsubscribe_at"])
    assert second_deadline > ready_at + timedelta(hours=47)
    assert datetime.fromisoformat(second.json()["ready_at"]) < second_deadline
    assert second.json()["retention_expires_at"] == second.json()["unsubscribe_at"]
    assert all(
        event.event_type is not TorrentEventType.RETENTION_EXTENDED for _, event in redis.events
    )

    db_session.expire_all()
    second_listing = await client.get("/api/v2/torrents")
    assert second_listing.status_code == 200
    second_item = second_listing.json()["items"][0]
    assert datetime.fromisoformat(second_item["unsubscribe_at"]) == second_deadline
    assert datetime.fromisoformat(second_item["ready_at"]) < second_deadline

    await login(client, owner_username)
    first_listing = await client.get("/api/v2/torrents")
    assert first_listing.status_code == 200
    first_item = first_listing.json()["items"][0]
    assert datetime.fromisoformat(first_item["ready_at"]) == ready_at
    assert datetime.fromisoformat(first_item["unsubscribe_at"]) == initial_deadline
    assert first_item["unsubscribe_at"] != second_item["unsubscribe_at"]
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
async def test_listing_excludes_terminal_and_overdue_subscriptions_from_items_and_total(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    expired_managed = ManagedTorrent(
        info_hash="9" * 40,
        name="Expired",
        total_size=100,
        state=ManagedTorrentState.READY,
        progress=1,
        ready_at=expired_at - timedelta(days=5),
        retention_expires_at=expired_at,
    )
    expired_request = TorrentRequest(
        user_id=owner.id,
        managed_torrent=expired_managed,
        state=TorrentRequestState.EXPIRED,
    )
    cancelled_managed = ManagedTorrent(
        info_hash="8" * 40,
        name="Cancelled",
        total_size=100,
        state=ManagedTorrentState.READY,
        progress=1,
    )
    overdue_managed = ManagedTorrent(
        info_hash="7" * 40,
        name="Overdue",
        total_size=100,
        state=ManagedTorrentState.READY,
        progress=1,
    )
    visible_managed = ManagedTorrent(
        info_hash="6" * 40,
        name="Visible",
        total_size=100,
        state=ManagedTorrentState.READY,
        progress=1,
    )
    visible_ready_at = expired_at - timedelta(hours=1)
    db_session.add_all(
        [
            expired_request,
            TorrentRequest(
                user_id=owner.id,
                managed_torrent=cancelled_managed,
                state=TorrentRequestState.CANCELLED,
            ),
            TorrentRequest(
                user_id=owner.id,
                managed_torrent=overdue_managed,
                state=TorrentRequestState.READY,
                ready_at=expired_at - timedelta(hours=2),
                unsubscribe_at=expired_at,
            ),
            TorrentRequest(
                user_id=owner.id,
                managed_torrent=visible_managed,
                state=TorrentRequestState.READY,
                ready_at=visible_ready_at,
                unsubscribe_at=expired_at + timedelta(days=1),
            ),
        ]
    )
    await db_session.commit()
    await login(client)

    response = await client.get("/api/v2/torrents")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["items"]] == ["Visible"]
    assert response.json()["total"] == 1
    assert datetime.fromisoformat(response.json()["items"][0]["ready_at"]) == visible_ready_at


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
    assert managed.state is ManagedTorrentState.READY
    assert managed.purge_after is not None
    assert managed.purge_stop_pending is False
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


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "cancel", "retry"])
@pytest.mark.parametrize("failure", ["connection", "pool"])
async def test_torrent_mutations_preserve_database_outage_contract(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure: str,
) -> None:
    from typing import Any

    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    from app.api.routes import torrents_v2
    from app.core.database_errors import DatabaseUnavailableError

    await prepare_user(db_session)
    headers = await login(client)
    error = DatabaseUnavailableError() if failure == "connection" else PoolTimeoutError("PRIVATE")
    called = 0

    async def unavailable(*args: Any, **kwargs: Any) -> Any:
        nonlocal called
        called += 1
        raise error

    if operation == "create":
        monkeypatch.setattr(torrents_v2, "create_or_get_torrent_request", unavailable)
        response = await client.post(
            "/api/v2/torrents",
            files={"torrent": ("test.torrent", torrent_content(), "application/x-bittorrent")},
            headers=headers,
        )
    elif operation == "cancel":
        monkeypatch.setattr(torrents_v2, "cancel_owned_torrent_request", unavailable)
        response = await client.delete(f"/api/v2/torrents/{uuid.uuid4()}", headers=headers)
    else:
        original = db_session.execute

        async def execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
            if any(
                getattr(column, "name", None) == "storage_key"
                for column in getattr(statement, "selected_columns", ())
            ):
                return await unavailable()
            return await original(statement, *args, **kwargs)

        monkeypatch.setattr(db_session, "execute", execute)
        response = await client.post(f"/api/v2/torrents/{uuid.uuid4()}/retry", headers=headers)
    assert called == 1
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_unavailable"
    assert response.headers["retry-after"] == "5"
    assert response.headers["cache-control"] == "no-store"
    assert "PRIVATE" not in response.text


@pytest.mark.asyncio
async def test_acknowledgement_lost_after_torrent_commit_keeps_durable_payload(
    client: AsyncClient, db_session: AsyncSession, data_root: Path
) -> None:
    from sqlalchemy import event

    from app.core.database_errors import DatabaseUnavailableError

    await prepare_user(db_session)
    headers = await login(client)
    commits = 0

    def lose_acknowledgement(session: object) -> None:
        nonlocal commits
        commits += 1
        # First commit is read-only authentication; second is the torrent transaction.
        if commits == 2:
            raise DatabaseUnavailableError()

    event.listen(db_session.sync_session, "after_commit", lose_acknowledgement)
    try:
        response = await client.post(
            "/api/v2/torrents",
            files={"torrent": ("test.torrent", torrent_content(), "application/x-bittorrent")},
            headers=headers,
        )
    finally:
        event.remove(db_session.sync_session, "after_commit", lose_acknowledgement)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_unavailable"
    assert commits == 2
    # Production get_db_session closes this uncertain request session before
    # the next caller reads the durable state. The shared fixture needs that
    # same close explicitly after simulating a lost commit acknowledgement.
    await db_session.close()
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(TorrentRequest)) == 1
    assert await db_session.scalar(select(func.count()).select_from(TorrentJob)) == 1
    assert len(list((data_root / "control" / "torrent-input").glob("*.torrent"))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["connection", "pool"])
async def test_precommit_outage_cleans_staged_payload_on_every_attempt(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from typing import Any

    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    from app.core.database_errors import DatabaseUnavailableError
    from app.jobs.torrent_payloads import TorrentPayloadStore

    await prepare_user(db_session)
    headers = await login(client)
    staged = False
    stages = 0
    original_stage = TorrentPayloadStore.stage
    original_flush = db_session.flush

    def stage(self: TorrentPayloadStore, *args: Any, **kwargs: Any) -> Any:
        nonlocal staged, stages
        result = original_stage(self, *args, **kwargs)
        stages += 1
        staged = True
        return result

    async def fail_job_flush(*args: Any, **kwargs: Any) -> None:
        nonlocal staged
        if staged:
            staged = False
            if failure == "connection":
                raise DatabaseUnavailableError()
            raise PoolTimeoutError("PRIVATE")
        await original_flush(*args, **kwargs)

    monkeypatch.setattr(TorrentPayloadStore, "stage", stage)
    monkeypatch.setattr(db_session, "flush", fail_job_flush)
    for attempt in range(2):
        response = await client.post(
            "/api/v2/torrents",
            files={"torrent": ("test.torrent", torrent_content(), "application/x-bittorrent")},
            headers=headers,
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "database_unavailable"
        assert response.headers["retry-after"] == "5"
        assert stages == attempt + 1
        assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0
        assert await db_session.scalar(select(func.count()).select_from(TorrentRequest)) == 0
        assert await db_session.scalar(select(func.count()).select_from(TorrentJob)) == 0
        assert list((data_root / "control" / "torrent-input").glob("*.torrent")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "original_state", [TorrentJobState.COMPLETED, None, TorrentJobState.FAILED]
)
async def test_sync_error_is_retried_by_observation_without_reading_a_removed_payload(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    original_state: TorrentJobState | None,
) -> None:
    from app.jobs.torrent_payloads import TorrentPayloadStore

    await prepare_user(db_session)
    headers = await login(client)
    redis = RecordingRedis()
    app.state.redis_coordinator = redis
    uploaded = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )
    assert uploaded.status_code == 201
    managed = await db_session.scalar(select(ManagedTorrent))
    job = await db_session.scalar(select(TorrentJob))
    assert managed is not None and job is not None
    managed.state = ManagedTorrentState.ERROR
    if original_state is None:
        await db_session.delete(job)
    else:
        job.state = original_state
        job.finished_at = datetime.now(UTC)
    if original_state is TorrentJobState.FAILED:
        # A newer successful retry supersedes the old failed ADD.
        db_session.add(
            TorrentJob(
                managed_torrent_id=managed.id,
                torrent_request_id=job.torrent_request_id,
                job_type=job.job_type,
                idempotency_key=f"completed-retry:{managed.id}",
                state=TorrentJobState.COMPLETED,
                available_at=datetime.now(UTC),
                created_at=job.created_at + timedelta(seconds=1),
                finished_at=datetime.now(UTC),
            )
        )
    await db_session.commit()
    redis.signals = 0
    redis.events.clear()

    def read(_store: TorrentPayloadStore, _key: uuid.UUID) -> Never:
        pytest.fail("An ineligible retry must not read the payload")

    monkeypatch.setattr(TorrentPayloadStore, "read", read)
    response = await client.post(
        f"/api/v2/torrents/{uploaded.json()['id']}/retry",
        headers=headers,
    )
    if original_state is None:
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "torrent_retry_not_allowed"
        assert redis.signals == 0
        assert redis.events == []
        return
    # The ADD job completed: the ERROR came from synchronization, so the torrent is observed
    # again in qBittorrent instead of replaying a payload that no longer exists.
    assert response.status_code == 200
    assert response.json()["state"] != "error"
    assert redis.signals == 0
    assert len(redis.events) == 1
    await db_session.refresh(managed)
    assert managed.state is ManagedTorrentState.PAUSED
    assert managed.desired_active is False


@pytest.mark.asyncio
async def test_add_job_attempt_budget_follows_the_retry_option(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await prepare_user(db_session)
    headers = await login(client)
    uploaded = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )
    assert uploaded.status_code == 201
    job = await db_session.scalar(select(TorrentJob))
    assert job is not None
    # WOS_TORRENT_MAX_RETRY_ATTEMPTS defaults to 5 retries, so six attempts in total.
    assert job.max_attempts == 6


@pytest.mark.asyncio
async def test_v2_upload_answers_503_when_the_stored_options_are_inconsistent(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await prepare_user(db_session)
    headers = await login(client)
    warning = await db_session.get(DatabaseOption, "WOS_STORAGE_PRESSURE_WARNING_PERCENT")
    assert warning is not None
    warning.integer_value = 95  # above the critical threshold
    await db_session.commit()

    response = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "torrent_options_unavailable"


async def _ready_request(
    db: AsyncSession, owner: User, *, number: int, retention_left: timedelta
) -> None:
    now = datetime.now(UTC)
    torrent = ManagedTorrent(
        info_hash=f"{number:040x}",
        name=f"ready-{number}",
        total_size=1,
        state=ManagedTorrentState.READY,
        progress=1,
        ready_at=now - timedelta(days=3),
    )
    db.add(torrent)
    await db.flush()
    db.add(
        TorrentRequest(
            user_id=owner.id,
            managed_torrent_id=torrent.id,
            state=TorrentRequestState.READY,
            ready_at=now - timedelta(days=3),
            unsubscribe_at=now + retention_left,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_an_expired_ready_request_no_longer_counts_against_the_active_limit(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    limit = await db_session.get(DatabaseOption, "WOS_TORRENT_MAX_ACTIVE_PER_USER")
    assert limit is not None
    limit.integer_value = 1
    await db_session.commit()
    await _ready_request(db_session, owner, number=0xAA, retention_left=-timedelta(minutes=40))
    headers = await login(client)

    response = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )

    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_a_live_ready_request_still_counts_against_the_active_limit(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner = await prepare_user(db_session)
    limit = await db_session.get(DatabaseOption, "WOS_TORRENT_MAX_ACTIVE_PER_USER")
    assert limit is not None
    limit.integer_value = 1
    await db_session.commit()
    await _ready_request(db_session, owner, number=0xBB, retention_left=timedelta(hours=10))
    headers = await login(client)

    response = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "torrent_limit_reached"
