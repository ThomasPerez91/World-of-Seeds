import getpass
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.cli as cli_module
from app.auth.security import hash_password
from app.cli import create_admin, migrate_workspaces
from app.core.config import Settings
from app.models import User


@pytest.mark.asyncio
async def test_create_admin_creates_the_direct_workspace(
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def fake_session_factory() -> AsyncIterator[AsyncSession]:
        yield db_session

    passwords = iter(["new-admin-password-long", "new-admin-password-long"])
    monkeypatch.setattr(cli_module, "session_factory", fake_session_factory)
    monkeypatch.setattr(cli_module, "get_settings", lambda: Settings(data_root=data_root))
    monkeypatch.setattr(getpass, "getpass", lambda _: next(passwords))

    await create_admin("admin")

    admin = await db_session.scalar(select(User).where(User.username == "admin"))
    assert admin is not None
    assert admin.is_admin is True
    assert admin.must_change_credentials is False
    assert {entry.name for entry in (data_root / "admin").iterdir()} == {"downloads"}


@pytest.mark.asyncio
async def test_create_admin_rejects_case_insensitive_username_collision(
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_session.add(User(username="Admin", password_hash=hash_password("existing-password-long")))
    await db_session.commit()

    @asynccontextmanager
    async def fake_session_factory() -> AsyncIterator[AsyncSession]:
        yield db_session

    passwords = iter(["new-admin-password-long", "new-admin-password-long"])
    monkeypatch.setattr(cli_module, "session_factory", fake_session_factory)
    monkeypatch.setattr(cli_module, "get_settings", lambda: Settings(data_root=data_root))
    monkeypatch.setattr(getpass, "getpass", lambda _: next(passwords))

    with pytest.raises(SystemExit, match="existe déjà"):
        await create_admin("admin")

    assert not (data_root / "admin").exists()


@pytest.mark.asyncio
async def test_migrate_workspaces_removes_only_an_empty_user_watch(
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_session.add(User(username="admin", password_hash=hash_password("existing-password-long")))
    await db_session.commit()
    workspace = data_root / "admin"
    (workspace / "downloads").mkdir(parents=True)
    (workspace / "watch").mkdir()

    @asynccontextmanager
    async def fake_session_factory() -> AsyncIterator[AsyncSession]:
        yield db_session

    monkeypatch.setattr(cli_module, "session_factory", fake_session_factory)
    monkeypatch.setattr(cli_module, "get_settings", lambda: Settings(data_root=data_root))

    await migrate_workspaces()

    assert (workspace / "downloads").is_dir()
    assert not (workspace / "watch").exists()
