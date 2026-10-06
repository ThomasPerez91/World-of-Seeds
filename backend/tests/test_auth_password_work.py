import asyncio
from threading import Event, Lock, get_ident
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import passwords
from app.auth.passwords import PasswordWorkPool, PasswordWorkUnavailableError
from app.auth.rate_limit import LoginIpRateLimiter
from app.auth.security import hash_password
from app.main import app
from app.models import User, UserSession


async def wait_started(event: Event) -> None:
    assert await asyncio.to_thread(event.wait, 2)


@pytest.mark.asyncio
async def test_password_work_keeps_loop_responsive_and_bounds_running_and_queued() -> None:
    pool = PasswordWorkPool(workers=2, max_pending=3)
    release = Event()
    both_started = Event()
    mutex = Lock()
    running = 0
    peak = 0

    def work() -> int:
        nonlocal running, peak
        with mutex:
            running += 1
            peak = max(peak, running)
            if running == 2:
                both_started.set()
        assert release.wait(3)
        with mutex:
            running -= 1
        return get_ident()

    tasks = [asyncio.create_task(pool.run(work)) for _ in range(3)]
    try:
        await wait_started(both_started)
        # Reaching here while both workers are blocked proves loop responsiveness.
        with pytest.raises(PasswordWorkUnavailableError):
            await pool.run(work)
        release.set()
        worker_ids = await asyncio.gather(*tasks)
        assert all(worker_id != get_ident() for worker_id in worker_ids)
        assert peak == 2
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pool.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_timeout", [False, True])
async def test_cancelled_or_timed_out_worker_retains_capacity(use_timeout: bool) -> None:
    pool = PasswordWorkPool(workers=1, max_pending=1, timeout_seconds=0.05 if use_timeout else 5)
    started, release = Event(), Event()

    def work() -> None:
        started.set()
        assert release.wait(3)

    task = asyncio.create_task(pool.run(work))
    try:
        await wait_started(started)
        if use_timeout:
            with pytest.raises(PasswordWorkUnavailableError):
                await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        with pytest.raises(PasswordWorkUnavailableError):
            await pool.run(lambda: None)
    finally:
        release.set()
        await pool.aclose()
    with pytest.raises(PasswordWorkUnavailableError):
        await pool.run(lambda: None)
    pool.start()
    assert await pool.run(lambda: "restarted") == "restarted"
    await pool.aclose()


@pytest.mark.asyncio
async def test_cancelled_queued_work_stays_bounded_and_skips_crypto_when_dequeued() -> None:
    pool = PasswordWorkPool(workers=1, max_pending=2)
    started, release = Event(), Event()

    def work() -> None:
        started.set()
        assert release.wait(3)

    first = asyncio.create_task(pool.run(work))
    queued = None
    try:
        await wait_started(started)
        unused = Event()
        queued = asyncio.create_task(pool.run(unused.set))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        # Repeated cancellations must not create unlimited internal executor entries.
        for _ in range(20):
            with pytest.raises(PasswordWorkUnavailableError):
                await pool.run(unused.set)
        release.set()
        await first
        # A sentinel completes after the abandoned entry is dequeued and skipped.
        assert await pool.run(lambda: "replacement") == "replacement"
        assert not unused.is_set()
    finally:
        release.set()
        await asyncio.gather(first, *([queued] if queued else []), return_exceptions=True)
        await pool.aclose()


@pytest.mark.asyncio
async def test_async_password_helpers_preserve_hash_compatibility() -> None:
    value = "correct-horse-battery"
    encoded = await passwords.hash_password_async(value)
    assert encoded.startswith("$argon2id$")
    assert await passwords.verify_password_async(value, encoded)
    assert not await passwords.verify_password_async("incorrect-password", encoded)


def test_ip_budget_expires_and_bounds_key_storage_without_eviction() -> None:
    now = 100.0
    limiter = LoginIpRateLimiter(maximum=2, window_seconds=60, max_keys=2, clock=lambda: now)
    assert limiter.consume("first") is None
    assert limiter.consume("first") is None
    assert limiter.consume("first") == 60
    assert limiter.consume("second") is None
    assert limiter.consume("third") == 60
    assert limiter.consume("first") == 60
    now += 60
    assert limiter.consume("third") is None
    assert limiter.consume("first") is None


@pytest.mark.asyncio
async def test_rotating_usernames_and_forwarded_headers_cannot_bypass_ip_budget(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr("app.auth.service.verify_password_async", verify)
    app.state.login_ip_rate_limiter = LoginIpRateLimiter(maximum=2)
    for index in range(2):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": f"unknown-{index}", "password": "incorrect-password"},
        )
        assert response.status_code == 401
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "another-unknown", "password": "incorrect-password"},
        headers={"X-Forwarded-For": "192.0.2.123"},
    )
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0
    assert verify.await_count == 2
    # The ASGI client address changes only through trusted server proxy handling.
    assert app.state.login_ip_rate_limiter.consume("different-client") is None


@pytest.mark.asyncio
async def test_pair_lock_rejects_before_crypto(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = AsyncMock(return_value=False)
    monkeypatch.setattr("app.auth.service.verify_password_async", verify)
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "unknown-user", "password": "incorrect-password"},
        )
        assert response.status_code == 401
    locked = await client.post(
        "/api/v1/auth/login",
        json={"username": "unknown-user", "password": "incorrect-password"},
    )
    assert locked.status_code == 429
    assert verify.await_count == 5


@pytest.mark.asyncio
async def test_password_overload_returns_retryable_generic_response(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.auth.service.verify_password_async",
        AsyncMock(side_effect=PasswordWorkUnavailableError),
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "unknown-user", "password": "incorrect-password"},
    )
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["detail"]["code"] == "authentication_unavailable"
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_password", [False, True])
async def test_account_change_during_verification_cannot_issue_session(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    changed_password: bool,
) -> None:
    user = User(username="changing-user", password_hash=hash_password("correct-horse-battery"))
    db_session.add(user)
    await db_session.commit()

    async def changed_while_verifying(value: str, encoded_hash: str) -> bool:
        if changed_password:
            user.password_hash = "changed-by-another-transaction"
        else:
            user.is_active = False
        await db_session.flush()
        return True

    monkeypatch.setattr("app.auth.service.verify_password_async", changed_while_verifying)
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": "correct-horse-battery"},
    )
    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert await db_session.scalar(select(UserSession)) is None
