import asyncio
import tracemalloc
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_external_api_v1 import _api_client, _headers, _user

from app.api.external.rate_limit import ExternalApiRateLimiter
from app.main import app


@pytest.fixture(autouse=True)
def isolated_limiter() -> Iterator[None]:
    app.state.external_api_rate_limiter = ExternalApiRateLimiter()
    yield
    app.state.external_api_rate_limiter = ExternalApiRateLimiter()


class Clock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("maximum", "window", "keys", "global_maximum"),
    [(0, 60, 100, 6000), (60, -1, 100, 6000), (60, 60, 0, 6000), (60, 60, 100, 0)],
)
def test_invalid_limits_are_rejected(
    maximum: int, window: int, keys: int, global_maximum: int
) -> None:
    with pytest.raises(ValueError):
        ExternalApiRateLimiter(
            maximum=maximum, window_seconds=window, max_keys=keys, global_maximum=global_maximum
        )


@pytest.mark.asyncio
async def test_rolling_window_and_retry_after_are_exact_at_the_boundary() -> None:
    clock = Clock()
    limiter = ExternalApiRateLimiter(maximum=2, clock=clock)
    assert await limiter.consume("ip:192.0.2.1") is None
    clock.now = 10
    assert await limiter.consume("ip:192.0.2.1") is None
    assert await limiter.consume("ip:192.0.2.1") == 50
    clock.now = 59.25
    assert await limiter.consume("ip:192.0.2.1") == 1
    clock.now = 60
    assert await limiter.consume("ip:192.0.2.1") is None
    assert await limiter.consume("ip:192.0.2.1") == 10


@pytest.mark.asyncio
async def test_expiry_cleans_keys_that_never_return_and_preserves_active_budgets() -> None:
    clock = Clock()
    limiter = ExternalApiRateLimiter(maximum=2, max_keys=2, clock=clock)
    assert await limiter.consume("ip:192.0.2.1") is None
    assert await limiter.consume("ip:192.0.2.1") is None
    clock.now = 10
    assert await limiter.consume("ip:192.0.2.2") is None
    clock.now = 20
    assert await limiter.consume("ip:192.0.2.1") == 40
    clock.now = 60
    assert await limiter.consume("ip:192.0.2.3") is None
    assert len(limiter._events) == 2
    assert await limiter.consume("ip:192.0.2.2") is None
    assert await limiter.consume("ip:192.0.2.2") == 10
    assert await limiter.consume("ip:192.0.2.4") == 60


@pytest.mark.asyncio
async def test_capacity_does_not_evict_or_reset_live_budgets() -> None:
    clock = Clock()
    limiter = ExternalApiRateLimiter(maximum=1, max_keys=2, clock=clock)
    for ip in ("192.0.2.1", "192.0.2.2"):
        assert await limiter.consume("ip:" + ip) is None
    for index in range(100):
        assert await limiter.consume(f"ip:2001:db8:{index:x}::1") == 60
    assert len(limiter._events) == 2
    assert await limiter.consume("ip:192.0.2.1") == 60
    assert await limiter.consume("ip:192.0.2.2") == 60
    clock.now = 60
    assert await limiter.consume("ip:192.0.2.3") is None
    assert len(limiter._events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "alias"),
    [
        ("2001:db8:1:2::1", "2001:0db8:0001:0002:ffff:ffff:ffff:ffff"),
        ("fe80::1%eth0", "fe80::2%eth1"),
        ("192.0.2.1", "::ffff:192.0.2.1"),
        ("unknown", "invalid-other-peer"),
    ],
)
async def test_address_aliases_and_ipv6_prefixes_share_the_budget(first: str, alias: str) -> None:
    limiter = ExternalApiRateLimiter(maximum=1)
    assert await limiter.consume("ip:" + first) is None
    assert await limiter.consume("ip:" + alias) is not None
    assert len(limiter._events) == 1
    assert first not in repr(limiter._events)
    assert alias not in repr(limiter._events)


@pytest.mark.asyncio
async def test_different_ipv6_prefixes_and_clients_have_independent_budgets() -> None:
    limiter = ExternalApiRateLimiter(maximum=1)
    for key in ("ip:2001:db8:1::1", "ip:2001:db8:2::1", "client:a", "client:b"):
        assert await limiter.consume(key) is None
        assert await limiter.consume(key) is not None


@pytest.mark.asyncio
async def test_global_budget_charges_ip_admission_once_and_has_exact_expiry() -> None:
    clock = Clock()
    limiter = ExternalApiRateLimiter(global_maximum=2, clock=clock)
    assert await limiter.consume("ip:192.0.2.1") is None
    assert await limiter.consume("client:a") is None
    assert await limiter.consume("ip:192.0.2.2") is None
    assert await limiter.consume("client:b") is None
    assert await limiter.consume("ip:192.0.2.3") == 60
    clock.now = 60
    assert await limiter.consume("ip:192.0.2.3") is None


@pytest.mark.asyncio
async def test_concurrent_admission_cannot_overspend_global_or_client_budget() -> None:
    limiter = ExternalApiRateLimiter(maximum=3, global_maximum=7)
    result = await asyncio.gather(*(limiter.consume("client:one") for _ in range(100)))
    assert result.count(None) == 3
    result = await asyncio.gather(
        *(limiter.consume(f"ip:2001:db8:{index:x}::1") for index in range(100))
    )
    assert result.count(None) == 7


@pytest.mark.asyncio
async def test_one_hundred_thousand_distinct_ipv6_prefixes_keep_memory_bounded() -> None:
    clock = Clock()
    limiter = ExternalApiRateLimiter(clock=clock)
    accepted = 0
    tracemalloc.start()
    try:
        for index in range(100_000):
            retry = await limiter.consume(f"ip:2001:db8:{index >> 16:x}:{index & 0xFFFF:x}::1")
            accepted += retry is None
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert accepted == limiter.global_maximum == 6000
    assert len(limiter._events) <= limiter.max_keys
    assert len(limiter._global_events) == 6000
    assert peak < 32 * 1024**2
    clock.now = 60
    assert await limiter.consume("ip:192.0.2.1") is None
    assert len(limiter._events) == len(limiter._global_events) == 1


@pytest.mark.asyncio
async def test_global_saturation_blocks_authentication_and_returns_retry_after(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _user(db_session, "rate-global-owner")
    _, raw = await _api_client(db_session, scopes=["downloads:read"])
    app.state.external_api_rate_limiter = ExternalApiRateLimiter(global_maximum=2)
    for index in range(3):
        async with AsyncClient(
            transport=ASGITransport(app=app, client=(f"192.0.2.{index + 1}", 1234)),
            base_url="http://test",
        ) as peer:
            headers = _headers(raw, owner.auth_seed) if index == 2 else _headers("invalid")
            response = await peer.get("/api/external/v1/me/downloads", headers=headers)
        assert response.status_code == (429 if index == 2 else 401)
    assert response.json()["error"]["code"] == "rate_limit_exceeded"
    assert 1 <= int(response.headers["retry-after"]) <= 60


@pytest.mark.asyncio
async def test_client_budget_survives_ip_rotation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _user(db_session, "rate-client-owner")
    _, raw = await _api_client(db_session, scopes=["downloads:read"])
    app.state.external_api_rate_limiter = ExternalApiRateLimiter(maximum=2)
    for index in range(3):
        async with AsyncClient(
            transport=ASGITransport(app=app, client=(f"2001:db8:{index:x}::1", 1234)),
            base_url="http://test",
        ) as peer:
            response = await peer.get(
                "/api/external/v1/me/downloads", headers=_headers(raw, owner.auth_seed)
            )
        assert response.status_code == (429 if index == 2 else 200), response.text
    assert response.json()["error"]["code"] == "rate_limit_exceeded"
