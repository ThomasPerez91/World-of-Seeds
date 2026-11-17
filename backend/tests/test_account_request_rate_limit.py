"""Per-account budget of authenticated business requests."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.request_rate_limit import (
    DEFAULT_MAXIMUM,
    OPTION_KEY,
    AccountRequestRateLimiter,
    business_rate_limit_exempt,
    is_business_rate_limit_exempt,
)
from app.auth.security import hash_password
from app.main import app
from app.models import DatabaseOption, User
from app.options import INACTIVE_OPTION_KEYS, PostgresOptionsRegistry
from app.options.registry import DATABASE_OPTION_SPECS


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeSession:
    def __init__(self, value: object) -> None:
        self.value = value
        self.reads = 0

    async def scalar(self, statement: Any) -> object:
        self.reads += 1
        return self.value


def test_the_option_is_wired_with_the_chosen_default() -> None:
    spec = next(spec for spec in DATABASE_OPTION_SPECS if spec.key == OPTION_KEY)
    assert spec.default == DEFAULT_MAXIMUM == 300
    assert OPTION_KEY not in INACTIVE_OPTION_KEYS


@pytest.mark.asyncio
async def test_budget_admits_the_maximum_then_names_the_wait() -> None:
    clock = Clock()
    limiter = AccountRequestRateLimiter(clock=clock)
    account = uuid.uuid4()

    assert [await limiter.consume(account, 10) for _ in range(10)] == [None] * 10
    clock.now += 15
    assert await limiter.consume(account, 10) == 45
    assert await limiter.consume(uuid.uuid4(), 10) is None


@pytest.mark.asyncio
async def test_previous_window_drains_progressively_instead_of_resetting() -> None:
    clock = Clock()
    limiter = AccountRequestRateLimiter(clock=clock)
    account = uuid.uuid4()
    for _ in range(10):
        assert await limiter.consume(account, 10) is None

    # At the window boundary the previous minute still weighs fully: no double burst.
    clock.now += 60
    assert await limiter.consume(account, 10) == 6
    clock.now += 6
    assert await limiter.consume(account, 10) is None
    assert await limiter.consume(account, 10) is not None

    # Two full windows later nothing is left of the burst.
    clock.now += 120
    assert [await limiter.consume(account, 10) for _ in range(10)] == [None] * 10


@pytest.mark.asyncio
async def test_least_recent_account_is_dropped_beyond_the_bound() -> None:
    clock = Clock()
    limiter = AccountRequestRateLimiter(clock=clock, max_accounts=2)
    first, second, third = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert await limiter.consume(first, 1) is None
    assert await limiter.consume(second, 1) is None
    assert await limiter.consume(third, 1) is None

    assert await limiter.consume(first, 1) is None
    assert await limiter.consume(third, 1) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(("stored", "expected"), [(120, 120), (None, DEFAULT_MAXIMUM)])
async def test_maximum_is_read_from_the_database_at_most_once_per_ttl(
    stored: int | None, expected: int
) -> None:
    clock = Clock()
    limiter = AccountRequestRateLimiter(clock=clock, option_ttl_seconds=30)
    session = FakeSession(stored)

    assert await limiter.maximum(session) == expected  # type: ignore[arg-type]
    clock.now += 29
    assert await limiter.maximum(session) == expected  # type: ignore[arg-type]
    assert session.reads == 1
    session.value = 50
    clock.now += 1
    assert await limiter.maximum(session) == 50  # type: ignore[arg-type]
    assert session.reads == 2


def test_only_marked_endpoints_are_exempt() -> None:
    async def business() -> None:
        return None

    @business_rate_limit_exempt
    async def transfer() -> None:
        return None

    assert not is_business_rate_limit_exempt(business)
    assert is_business_rate_limit_exempt(transfer)
    assert not is_business_rate_limit_exempt(None)


def test_file_transfer_routes_are_exempt_and_business_routes_are_not() -> None:
    endpoints = {
        route.path: route.endpoint  # type: ignore[attr-defined]
        for route in app.routes
        if hasattr(route, "endpoint")
    }
    exempt = {
        path for path, endpoint in endpoints.items() if is_business_rate_limit_exempt(endpoint)
    }

    assert exempt == {
        "/api/v2/torrents/{torrent_request_id}/download-folder-archive",
        "/api/v2/torrents/{torrent_request_id}/download-archive",
        "/api/v2/torrents/{torrent_request_id}/files/{torrent_file_id}/download",
    }


async def _login(client: AsyncClient, db: AsyncSession, username: str) -> None:
    db.add(User(username=username, password_hash=hash_password("correct-horse-battery")))
    await db.commit()
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text


async def _set_budget(db: AsyncSession, maximum: int) -> None:
    await PostgresOptionsRegistry().initialize(db)
    option = await db.scalar(select(DatabaseOption).where(DatabaseOption.key == OPTION_KEY))
    assert option is not None
    option.integer_value = maximum
    await db.commit()


@pytest.mark.asyncio
async def test_authenticated_requests_beyond_the_budget_get_429_with_retry_after(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _set_budget(db_session, 10)
    await _login(client, db_session, "busy-account")

    for _ in range(10):
        assert (await client.get("/api/v1/auth/me")).status_code == 200
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "request_rate_limited"
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_file_transfers_stay_available_when_the_business_budget_is_spent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _set_budget(db_session, 10)
    await _login(client, db_session, "folder-downloader")
    for _ in range(10):
        assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 429

    response = await client.get(f"/api/v2/torrents/{uuid.uuid4()}/files/{uuid.uuid4()}/download")

    assert response.status_code != 429


@pytest.mark.asyncio
async def test_each_account_has_its_own_budget(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _set_budget(db_session, 10)
    await _login(client, db_session, "first-account")
    for _ in range(10):
        assert (await client.get("/api/v1/auth/me")).status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 429

    client.cookies.clear()
    await _login(client, db_session, "second-account")
    assert (await client.get("/api/v1/auth/me")).status_code == 200
