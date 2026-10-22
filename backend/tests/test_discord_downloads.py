from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_external_api_v1 import _api_client, _headers, _user

from app.api.external.downloads import _state, warning_at
from app.api.external.rate_limit import ExternalApiRateLimiter
from app.main import app
from app.models import (
    DiscordAccountLink,
    ExternalApiClient,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentRequest,
    TorrentRequestState,
    User,
)

ROOT = "/api/external/v1"
MEMBER = "123456789012345678"
OTHER = "223456789012345678"


@pytest.fixture(autouse=True)
def reset_limiter() -> None:
    app.state.external_api_rate_limiter = ExternalApiRateLimiter()


async def fixture(
    db: AsyncSession,
) -> tuple[str, ExternalApiClient, User, User, TorrentRequest, TorrentRequest, ManagedTorrent]:
    api, raw = await _api_client(db, scopes=["downloads:read-discord", "downloads:read"])
    owner = await _user(db, "download-owner")
    other = await _user(db, "download-other")
    db.add_all(
        [
            DiscordAccountLink(discord_user_id=m, user_id=u.id, client_id=api.id)
            for m, u in [(MEMBER, owner), (OTHER, other)]
        ]
    )
    torrent = ManagedTorrent(
        info_hash="a" * 40,
        name="Shared film",
        total_size=123,
        state=ManagedTorrentState.READY,
        progress=1,
    )
    db.add(torrent)
    await db.flush()
    now = datetime.now(UTC)
    first = TorrentRequest(
        user_id=owner.id,
        managed_torrent_id=torrent.id,
        state=TorrentRequestState.READY,
        ready_at=now - timedelta(hours=8),
        unsubscribe_at=now + timedelta(hours=1),
    )
    second = TorrentRequest(
        user_id=other.id,
        managed_torrent_id=torrent.id,
        state=TorrentRequestState.READY,
        ready_at=now,
        unsubscribe_at=now + timedelta(hours=9),
    )
    db.add_all([first, second])
    await db.commit()
    return raw, api, owner, other, first, second, torrent


@pytest.mark.asyncio
async def test_discord_list_uses_only_personal_rights_and_warning(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, _, owner, _, first, _, _ = await fixture(db_session)
    result = await client.get(f"{ROOT}/users/discord/{MEMBER}/downloads", headers=_headers(raw))
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    page = result.json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == str(first.id)
    assert page["items"][0]["access_expiring_soon"] is True
    assert "auth_seed" not in result.text and owner.auth_seed not in result.text
    own = await client.get(f"{ROOT}/me/downloads", headers=_headers(raw, owner.auth_seed))
    assert own.json()["items"] == page["items"]
    other = await client.get(f"{ROOT}/users/discord/{OTHER}/downloads", headers=_headers(raw))
    assert other.json()["items"][0]["access_expiring_soon"] is False
    assert (
        await client.get(
            f"{ROOT}/users/discord/{MEMBER}/downloads?limit=101", headers=_headers(raw)
        )
    ).status_code == 422


@pytest.mark.asyncio
async def test_discord_scope_is_explicit_and_revocable(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, api, _, _, _, _, _ = await fixture(db_session)
    api.scopes = ["downloads:read", "users:read"]
    await db_session.commit()
    for path in [f"users/discord/{MEMBER}/downloads", "discord/download-notices"]:
        result = await client.get(f"{ROOT}/{path}", headers=_headers(raw))
        assert result.status_code == 403
        assert result.json()["error"]["code"] == "insufficient_scope"
    api.scopes = ["downloads:read-discord"]
    api.is_active = False
    await db_session.commit()
    assert (
        await client.get(f"{ROOT}/discord/download-notices", headers=_headers(raw))
    ).status_code == 401


@pytest.mark.asyncio
async def test_notices_keyset_pagination_and_disabled_deleted_cancelled_expired_filter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, _, owner, other, first, second, _ = await fixture(db_session)
    pages = []
    after = ""
    while True:
        result = await client.get(
            f"{ROOT}/discord/download-notices?limit=1{after}", headers=_headers(raw)
        )
        assert result.status_code == 200, result.text
        page = result.json()
        pages.extend(page["items"])
        if page["next_after"] is None:
            break
        after = "&after=" + page["next_after"]
    assert {row["request_id"] for row in pages} == {str(first.id), str(second.id)}
    assert len(pages) == 2
    owner.is_active = False
    second.state = TorrentRequestState.CANCELLED
    await db_session.commit()
    assert (await client.get(f"{ROOT}/discord/download-notices", headers=_headers(raw))).json()[
        "items"
    ] == []
    assert (
        await client.get(f"{ROOT}/users/discord/{MEMBER}/downloads", headers=_headers(raw))
    ).status_code == 403
    owner.is_active = True
    owner.deleted_at = datetime.now(UTC)
    second.state = TorrentRequestState.READY
    second.unsubscribe_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    assert (await client.get(f"{ROOT}/discord/download-notices", headers=_headers(raw))).json()[
        "items"
    ] == []
    assert (
        await client.get(f"{ROOT}/users/discord/{MEMBER}/downloads", headers=_headers(raw))
    ).status_code == 404
    expired = await client.get(f"{ROOT}/users/discord/{OTHER}/downloads", headers=_headers(raw))
    assert expired.json()["items"][0]["state"] == "expired"
    assert expired.json()["items"][0]["access_expiring_soon"] is False


@pytest.mark.asyncio
async def test_queue_position_matches_physical_shared_backlog(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, _, owner, other, first, second, torrent = await fixture(db_session)
    torrent.state = ManagedTorrentState.PAUSED
    torrent.progress = 0.5
    torrent.desired_active = False
    first.state = second.state = TorrentRequestState.ACTIVE
    earlier = ManagedTorrent(
        info_hash="b" * 40,
        name="Invisible other film",
        total_size=5,
        state=ManagedTorrentState.PAUSED,
        desired_active=False,
        created_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(earlier)
    await db_session.flush()
    db_session.add(
        TorrentRequest(
            user_id=other.id, managed_torrent_id=earlier.id, state=TorrentRequestState.ACTIVE
        )
    )
    await db_session.commit()
    result = await client.get(f"{ROOT}/users/discord/{MEMBER}/downloads", headers=_headers(raw))
    item = result.json()["items"][0]
    assert item["state"] == "waiting" and item["queue_position"] == 2
    assert "Invisible other film" not in result.text
    torrent.desired_active = True
    torrent.desired_priority = 0
    await db_session.commit()
    result = await client.get(f"{ROOT}/me/downloads", headers=_headers(raw, owner.auth_seed))
    assert result.json()["items"][0]["state"] == "downloading"
    assert result.json()["items"][0]["queue_position"] is None


def test_warning_is_exactly_last_third_and_terminal_state_survives_physical_error() -> None:
    now = datetime.now(UTC)
    request = TorrentRequest(
        user_id=uuid4(),
        managed_torrent_id=uuid4(),
        state=TorrentRequestState.CANCELLED,
        ready_at=now,
        unsubscribe_at=now + timedelta(hours=9),
    )
    assert warning_at(request) == now + timedelta(hours=6)
    torrent = ManagedTorrent(state=ManagedTorrentState.ERROR)
    assert _state(request, torrent, now) == "cancelled"
    request.state = TorrentRequestState.EXPIRED
    assert _state(request, torrent, now) == "expired"
    request.ready_at = None
    assert warning_at(request) is None
