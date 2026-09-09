import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.models import User


@pytest.mark.asyncio
async def test_shared_storage_capacity_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v2/storage")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_shared_storage_capacity_exposes_only_capacity(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    db_session.add(
        User(
            username="thomas",
            password_hash=hash_password("correct-horse-battery"),
        )
    )
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "thomas", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200

    response = await client.get("/api/v2/storage")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"total_bytes", "used_bytes", "available_bytes"}
    assert payload["total_bytes"] > 0
    assert payload["used_bytes"] >= 0
    assert payload["available_bytes"] > 0
    assert payload["used_bytes"] + payload["available_bytes"] <= payload["total_bytes"]
