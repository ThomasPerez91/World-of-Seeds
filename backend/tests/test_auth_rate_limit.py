import tracemalloc
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_auth import create_user
from test_password_reset import CONFIRM, issue, setup

from app.auth.rate_limit import LoginIpRateLimiter
from app.main import app


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    "argument",
    [
        "maximum",
        "window_seconds",
        "max_keys",
        "global_maximum",
        "fallback_maximum",
        "overflow_buckets",
    ],
)
def test_invalid_budgets(argument: str) -> None:
    with pytest.raises(ValueError):
        LoginIpRateLimiter(
            maximum=0 if argument == "maximum" else 30,
            window_seconds=0 if argument == "window_seconds" else 60,
            max_keys=0 if argument == "max_keys" else 10_000,
            global_maximum=0 if argument == "global_maximum" else 6_000,
            fallback_maximum=0 if argument == "fallback_maximum" else 5,
            overflow_buckets=0 if argument == "overflow_buckets" else 4_096,
        )


@pytest.mark.parametrize(
    ("first", "alias"),
    [
        ("2001:db8:1:2::1", "2001:0db8:1:2:ffff::2"),
        ("fe80::1%eth0", "fe80::2%eth1"),
        ("192.0.2.1", "::ffff:192.0.2.1"),
        ("unknown", "arbitrary-peer"),
    ],
)
def test_network_aliases_share_one_budget(first: str, alias: str) -> None:
    limiter = LoginIpRateLimiter(maximum=1)
    assert limiter.consume(first, identity="first") is None
    assert limiter.consume(alias, identity="different") is not None
    assert len(limiter._windows) == 1
    assert first not in repr(limiter._windows)


def test_full_primary_table_allows_new_identity_without_evicting_existing_budget() -> None:
    clock = Clock()
    limiter = LoginIpRateLimiter(maximum=2, max_keys=1, clock=clock)
    assert limiter.consume("192.0.2.1", identity="old") is None
    assert limiter.consume("192.0.2.1", identity="old") is None
    assert limiter.consume("192.0.2.2", identity="new") is None
    assert limiter.consume("192.0.2.1", identity="old") == 60
    assert len(limiter._windows) == 1
    clock.now = 59.25
    assert limiter.consume("192.0.2.1", identity="old") == 1
    clock.now = 60
    assert limiter.consume("192.0.2.1", identity="old") is None


def test_overflow_cannot_rotate_names_or_ips_to_reset_live_budgets() -> None:
    clock = Clock()
    limiter = LoginIpRateLimiter(maximum=2, fallback_maximum=2, max_keys=1, clock=clock)
    assert limiter.consume("192.0.2.1", identity="admitted") is None
    assert limiter.consume("192.0.2.2", identity="target") is None
    assert limiter.consume("192.0.2.3", identity="target") is None
    # Switching back to an admitted IP must not escape the identity budget.
    assert limiter.consume("192.0.2.1", identity="target") == 60
    assert limiter.consume("192.0.2.2", identity="different-name") is None
    assert limiter.consume("192.0.2.2", identity="another-name") == 60


def test_primary_expiration_does_not_reset_still_live_overflow_budget() -> None:
    clock = Clock()
    limiter = LoginIpRateLimiter(maximum=2, fallback_maximum=10, max_keys=1, clock=clock)
    assert limiter.consume("192.0.2.1", identity="old") is None
    clock.now = 10
    assert limiter.consume("192.0.2.2", identity="new") is None
    assert limiter.consume("192.0.2.2", identity="new") is None
    clock.now = 60
    assert limiter.consume("192.0.2.2", identity="new") == 10
    assert not limiter._windows
    clock.now = 70
    assert limiter.consume("192.0.2.2", identity="new") is None


def test_overflow_collisions_restrict_instead_of_resetting_and_expire_exactly() -> None:
    clock = Clock()
    limiter = LoginIpRateLimiter(max_keys=1, overflow_buckets=1, fallback_maximum=2, clock=clock)
    assert limiter.consume("192.0.2.1", identity="old") is None
    assert limiter.consume("192.0.2.2", identity="first") is None
    assert limiter.consume("192.0.2.3", identity="second") is None
    assert limiter.consume("192.0.2.4", identity="third") == 60
    clock.now = 60
    assert limiter.consume("192.0.2.4", identity="third") is None


def test_global_budget_bounds_rotating_networks_and_identities_before_expiry() -> None:
    clock = Clock()
    limiter = LoginIpRateLimiter(global_maximum=3, max_keys=1, clock=clock)
    for index in range(3):
        assert limiter.consume(f"2001:db8:{index:x}::1", identity=f"name-{index}") is None
    for index in range(100_000):
        assert limiter.consume(f"2001:db8:{index:x}::2", identity=f"name-{index}") == 60
    assert len(limiter._windows) == 1
    assert len(limiter._overflow_ips) == len(limiter._overflow_identities) == 4096
    clock.now = 59.25
    assert limiter.consume("192.0.2.4", identity="new") == 1
    clock.now = 60
    assert limiter.consume("192.0.2.4", identity="new") is None


def test_many_distinct_networks_and_accounts_keep_overflow_memory_bounded() -> None:
    limiter = LoginIpRateLimiter(max_keys=8, global_maximum=200_000, clock=Clock())
    tracemalloc.start()
    try:
        for index in range(100_000):
            limiter.consume(
                f"2001:db8:{index >> 16:x}:{index & 65535:x}::1", identity=f"name-{index}"
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(limiter._windows) == 8
    assert len(limiter._overflow_ips) == len(limiter._overflow_identities) == 4096
    assert limiter._global is not None
    assert limiter._global.count <= 8 + 4096 * limiter.fallback_maximum
    assert peak < 32 * 1024 * 1024


@pytest.mark.asyncio
@pytest.mark.parametrize("is_admin", [False, True])
async def test_valid_login_from_new_ip_when_primary_table_is_full(
    client: AsyncClient, db_session: AsyncSession, is_admin: bool
) -> None:
    await create_user(
        db_session, username="valid-user", password="correct-password", is_admin=is_admin
    )
    limiter = LoginIpRateLimiter(max_keys=1)
    assert limiter.consume("192.0.2.1", identity="unrelated") is None
    app.state.login_ip_rate_limiter = limiter
    response = await client.post(
        "/api/v1/auth/login", json={"username": "valid-user", "password": "correct-password"}
    )
    assert response.status_code == 200
    assert client.cookies.get("wos_session")
    assert len(limiter._windows) == 1


@pytest.mark.asyncio
async def test_overflow_login_aliases_and_network_rotation_stop_before_crypto(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr("app.auth.service.verify_password_async", verify)
    limiter = LoginIpRateLimiter(max_keys=1, fallback_maximum=2)
    assert limiter.consume("192.0.2.1", identity="unrelated") is None
    app.state.login_ip_rate_limiter = limiter
    for index, username in enumerate(("target-user", " TARGET-USER ", "Target-User")):
        async with AsyncClient(
            transport=ASGITransport(app=app, client=(f"2001:db8:{index:x}::1", 1234)),
            base_url="http://test",
        ) as peer:
            response = await peer.post(
                "/api/v1/auth/login", json={"username": username, "password": "wrong-password"}
            )
        assert response.status_code == (401 if index < 2 else 429)
    assert verify.await_count == 2
    assert int(response.headers["Retry-After"]) > 0
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_login_and_reset_have_independent_global_budgets(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await create_user(db_session, username="valid-user", password="correct-password")
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr("app.auth.service.verify_password_async", verify)
    app.state.login_ip_rate_limiter = LoginIpRateLimiter(global_maximum=1)
    first = await client.post(
        "/api/v1/auth/login", json={"username": "unknown", "password": "wrong-password"}
    )
    assert first.status_code == 401
    blocked = await client.post(
        "/api/v1/auth/login", json={"username": "valid-user", "password": "correct-password"}
    )
    assert blocked.status_code == 429
    assert verify.await_count == 1
    # Login flooding cannot consume the independent public reset budget.
    app.state.password_reset_ip_rate_limiter = LoginIpRateLimiter(global_maximum=1)
    response = await client.post(
        CONFIRM, json={"token": "Z" * 43, "new_password": "replacement-password"}
    )
    assert response.status_code == 400
    assert (
        await client.post(CONFIRM, json={"token": "Z" * 43, "new_password": "replacement-password"})
    ).status_code == 429
    # Reset flooding cannot consume login budget either.
    app.state.login_ip_rate_limiter = LoginIpRateLimiter()
    assert (
        await client.post(
            "/api/v1/auth/login", json={"username": "other", "password": "wrong-password"}
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_valid_reset_from_new_ip_with_full_table_still_consumes_grant(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    raw, _ = await setup(client, db_session)
    token = await issue(client, raw)
    limiter = LoginIpRateLimiter(max_keys=1)
    assert limiter.consume("192.0.2.1", identity="unrelated") is None
    app.state.password_reset_ip_rate_limiter = limiter
    response = await client.post(
        CONFIRM, json={"token": token, "new_password": "replacement-password"}
    )
    assert response.status_code == 204
    replay = await client.post(
        CONFIRM, json={"token": token, "new_password": "replacement-password"}
    )
    assert replay.status_code == 400
