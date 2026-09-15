"""Create disposable local credentials and initialize V2 SQL options."""

from __future__ import annotations

import asyncio
import json
import os
import secrets

from sqlalchemy import func, select

from app.auth.security import hash_password
from app.core.database import engine, session_factory
from app.models import User
from app.options import PostgresOptionsRegistry

USERNAME = "local-smoke"


async def seed() -> dict[str, str]:
    if os.environ.get("WOS_ENVIRONMENT") != "development":
        raise RuntimeError("local smoke seed is restricted to development")
    password = secrets.token_urlsafe(24)
    async with session_factory() as session, session.begin():
        await PostgresOptionsRegistry().initialize(session)
        user = await session.scalar(select(User).where(func.lower(User.username) == USERNAME))
        if user is None:
            user = User(username=USERNAME, password_hash=hash_password(password), is_admin=True)
            session.add(user)
            await session.flush()
        else:
            user.password_hash = hash_password(password)
            user.is_active = True
            user.must_change_credentials = False
            user.deleted_at = None
            user.is_admin = True
        await PostgresOptionsRegistry().update(
            session,
            {
                "WOS_C411_ACCOUNT_01_NUMBER": "1",
                "WOS_C411_ACCOUNT_01_PASSKEY": "local-test-passkey",
            },
            actor_user_id=user.id,
        )
        user.is_admin = False
    return {"username": USERNAME, "password": password}


async def _main() -> None:
    try:
        print(json.dumps(await seed(), separators=(",", ":")))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
