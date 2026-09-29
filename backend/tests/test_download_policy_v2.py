import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.main import app
from app.models import User
from app.options import PostgresOptionsRegistry
from app.torrents.traffic import DownloadTrafficScheduler


@pytest.mark.asyncio
async def test_download_policy_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v2/downloads/policy")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_download_policy_delegates_file_stream_admission_to_server(
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
    assert response.json() == {"max_concurrent_streams": 2, "unlimited": False}

    scheduler = app.state.download_traffic_scheduler
    assert isinstance(scheduler, DownloadTrafficScheduler)
    user = (await db_session.scalars(select(User).where(User.username == "download-user"))).one()
    lease_id = uuid.uuid4()
    await scheduler.register(lease_id, user.id, 20_000_000_000)
    traffic = await client.get("/api/v2/downloads/traffic")
    assert traffic.status_code == 200
    assert traffic.json()["my_fast_streams"] == 1
    assert traffic.json()["fast_limit"] == 5
    await scheduler.unregister(lease_id)


@pytest.mark.asyncio
async def test_download_policy_keeps_two_local_streams_for_active_admin(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    db_session.add(
        User(
            username="download-admin",
            password_hash=hash_password("correct-horse-battery"),
            is_admin=True,
        )
    )
    await PostgresOptionsRegistry().initialize(db_session)
    await db_session.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "download-admin", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200

    response = await client.get("/api/v2/downloads/policy")

    assert response.status_code == 200
    assert response.json() == {"max_concurrent_streams": 2, "unlimited": False}
