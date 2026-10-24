import ipaddress

import pytest
from httpx import AsyncClient
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password, network_identity, throttle_key
from app.models import User

PASSWORD = "correct-horse-battery"


def test_throttle_key_is_per_ipv6_subscriber_not_per_address() -> None:
    same_subnet = [
        throttle_key("2001:db8:1:2::1", "Alice"),
        throttle_key("2001:db8:1:2:ffff:ffff:ffff:ffff", "alice"),
        throttle_key("2001:db8:1:2::1%eth0", "ALICE"),
    ]
    assert len(set(same_subnet)) == 1
    assert throttle_key("2001:db8:1:3::1", "alice") != same_subnet[0]
    assert throttle_key("2001:db8:1:2::1", "bob") != same_subnet[0]


def test_ipv4_addresses_stay_individual_and_mapped_addresses_are_unwrapped() -> None:
    assert throttle_key("203.0.113.7", "alice") != throttle_key("203.0.113.8", "alice")
    assert network_identity("::ffff:203.0.113.7") == "203.0.113.7"
    assert network_identity("not an address") == "unknown"
    assert ipaddress.ip_network(network_identity("2001:db8::1")).prefixlen == 64


@pytest.mark.asyncio
async def test_locked_login_tells_the_client_when_to_retry(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    db_session.add(User(username="lockme", password_hash=hash_password(PASSWORD)))
    await db_session.commit()

    statuses: list[int] = []
    last = None
    for _ in range(7):
        last = await client.post(
            "/api/v1/auth/login", json={"username": "lockme", "password": "wrong-password-12"}
        )
        statuses.append(last.status_code)

    assert 429 in statuses
    assert last is not None and last.status_code == 429
    assert int(last.headers["retry-after"]) >= 1
    assert last.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_login_refreshes_a_password_hash_made_with_outdated_parameters(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    weak = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=1024, parallelism=1),)).hash(
        PASSWORD
    )
    user = User(username="olderhash", password_hash=weak)
    db_session.add(user)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/login", json={"username": "olderhash", "password": PASSWORD}
    )

    assert response.status_code == 200
    await db_session.refresh(user)
    assert user.password_hash != weak
    assert "m=65536" in user.password_hash
    again = await client.post(
        "/api/v1/auth/login", json={"username": "olderhash", "password": PASSWORD}
    )
    assert again.status_code == 200
