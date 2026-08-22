from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.models import DatabaseOptionAudit, SchedulerState, StorageLedger, User, UserStorageUsage
from app.options import PostgresOptionsRegistry


async def _admin(db: AsyncSession) -> User:
    user = User(
        username="central-admin",
        password_hash=hash_password("correct-horse-battery"),
        is_admin=True,
    )
    db.add(user)
    await PostgresOptionsRegistry().initialize(db)
    db.add_all(
        [
            SchedulerState(
                id=1,
                desired_generation=4,
                applied_generation=3,
                rounds=9,
                lease_owner="scheduler-test",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
            ),
            StorageLedger(
                id=1,
                managed_bytes=100,
                disk_total_bytes=1000,
                disk_free_bytes=600,
            ),
            UserStorageUsage(user_id=user.id, logical_bytes=150),
        ]
    )
    await db.commit()
    return user


async def _login(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "central-admin", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    token = client.cookies.get("wos_csrf")
    assert token is not None
    return {"X-CSRF-Token": token}


@pytest.mark.asyncio
async def test_admin_overview_exposes_options_scheduler_storage_and_bounded_audit(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _admin(db_session)
    await _login(client)

    response = await client.get("/api/v2/admin/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["scheduler"] == {
        "desired_generation": 4,
        "applied_generation": 3,
        "synchronized": False,
        "rounds": 9,
        "lease_active": True,
    }
    assert body["storage"]["managed_bytes"] == 100
    assert body["storage"]["logical_bytes"] == 150
    assert 0 < len(body["audit"]) <= 50
    assert all(
        "PASSKEY" not in field["key"] for section in body["sections"] for field in section["fields"]
    )


@pytest.mark.asyncio
async def test_admin_option_update_requires_csrf_and_records_actor(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    admin = await _admin(db_session)
    headers = await _login(client)
    payload = {"changes": {"WOS_STORAGE_USER_MAX_BYTES": 1024}}

    rejected = await client.patch("/api/v2/admin/options", json=payload)
    accepted = await client.patch("/api/v2/admin/options", json=payload, headers=headers)

    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["changed_keys"] == ["WOS_STORAGE_USER_MAX_BYTES"]
    assert accepted.json()["storage"]["user_quota_bytes"] == 1024
    audit = await db_session.scalar(
        select(DatabaseOptionAudit)
        .where(DatabaseOptionAudit.option_key == "WOS_STORAGE_USER_MAX_BYTES")
        .order_by(DatabaseOptionAudit.version.desc())
    )
    assert audit is not None
    assert audit.actor_user_id == admin.id
