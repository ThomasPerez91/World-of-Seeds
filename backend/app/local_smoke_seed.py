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
            user = User(username=USERNAME, password_hash=hash_password(password))
            session.add(user)
        else:
            user.password_hash = hash_password(password)
            user.is_admin = False
            user.is_active = True
            user.must_change_credentials = False
            user.deleted_at = None
    return {"username": USERNAME, "password": password}


async def _main() -> None:
    try:
        print(json.dumps(await seed(), separators=(",", ":")))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
