import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.models import User
from app.options import PostgresOptionsRegistry


@pytest.mark.asyncio
async def test_download_policy_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v2/downloads/policy")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_download_policy_exposes_configured_stream_limit(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    db_session.add(
        User(
            username="download-user",
            password_hash=hash_password("correct-horse-battery"),
        )
    )
    await PostgresOptionsRegistry().initialize(db_session)
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "download-user", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200

    response = await client.get("/api/v2/downloads/policy")

    assert response.status_code == 200
    assert response.json() == {"max_concurrent_streams": 2}
