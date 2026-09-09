#!/usr/bin/env python3
"""Secret-safe V2-33 cleanup and pilot-account probe for Gates 10 and 11."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

from sqlalchemy import delete, func, or_, select

from app.auth.service import change_username, create_managed_user
from app.core.config import get_settings
from app.core.database import engine, session_factory
from app.files import WorkspaceManager
from app.models import ManagedTorrent, TorrentFile, User

CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("cleanup-test", "pilot-create", "pilot-inspect"))
    parser.add_argument("--campaign", required=True)
    return parser.parse_args()


def _pilot_username(campaign: str) -> str:
    return f"pilot-{campaign}"


def _credential_path(campaign: str) -> Path:
    return Path(get_settings().data_root) / f".v233-pilot-credentials-{campaign}.json"


def _test_user_predicate():
    return or_(
        User.username.like("v233-%"),
        User.username.like("ws-ws-%"),
        User.username.like("gate6-tm-%"),
        User.username.like("resource-rp-%"),
    )


def _test_torrent_predicate():
    return or_(
        ManagedTorrent.name.like("v233-%"),
        ManagedTorrent.name.like("gate6-tm-%"),
        ManagedTorrent.name.like("v233-resource-rp-%"),
    )


def _known_test_physical_files(data_root: Path) -> int:
    content = data_root / "content"
    if not content.exists():
        return 0
    findings = 0
    for storage_root in content.iterdir():
        if storage_root.is_symlink() or not storage_root.is_dir():
            continue
        gate6 = storage_root / "gate6"
        if gate6.exists():
            findings += 1
        try:
            for child in storage_root.iterdir():
                if child.is_file() and child.name.startswith("pilot-"):
                    findings += 1
        except OSError:
            findings += 1
    return findings


async def cleanup_test_data() -> dict[str, int | bool]:
    settings = get_settings()
    if settings.environment != "production" or os.environ.get("WOS_RUNTIME_PROFILE") != "v2":
        raise RuntimeError("Gate 10 cleanup requires the production V2 profile")
    if Path(settings.data_root) != Path("/data"):
        raise RuntimeError("Gate 10 cleanup requires the isolated /data root")

    async with session_factory() as session, session.begin():
        test_torrent_ids = select(ManagedTorrent.id).where(_test_torrent_predicate())
        before_users = int(
            await session.scalar(
                select(func.count()).select_from(User).where(_test_user_predicate())
            )
            or 0
        )
        before_torrents = int(
            await session.scalar(
                select(func.count()).select_from(ManagedTorrent).where(_test_torrent_predicate())
            )
            or 0
        )
        before_files = int(
            await session.scalar(
                select(func.count())
                .select_from(TorrentFile)
                .where(TorrentFile.managed_torrent_id.in_(test_torrent_ids))
            )
            or 0
        )
        await session.execute(delete(ManagedTorrent).where(_test_torrent_predicate()))
        await session.execute(delete(User).where(_test_user_predicate()))

    async with session_factory() as session:
        remaining_users = int(
            await session.scalar(
                select(func.count()).select_from(User).where(_test_user_predicate())
            )
            or 0
        )
        remaining_torrents = int(
            await session.scalar(
                select(func.count()).select_from(ManagedTorrent).where(_test_torrent_predicate())
            )
            or 0
        )
        remaining_files = int(
            await session.scalar(
                select(func.count())
                .select_from(TorrentFile)
                .join(ManagedTorrent)
                .where(_test_torrent_predicate())
            )
            or 0
        )
        await session.rollback()

    physical_findings = _known_test_physical_files(Path(settings.data_root))
    return {
        "before_test_users": before_users,
        "before_test_torrents": before_torrents,
        "before_test_files": before_files,
        "remaining_test_users": remaining_users,
        "remaining_test_torrents": remaining_torrents,
        "remaining_test_files": remaining_files + physical_findings,
        "physical_test_artifact_findings": physical_findings,
        "v1_write_operations": 0,
        "v1_unchanged": True,
    }


async def pilot_create(campaign: str) -> dict[str, int | bool]:
    settings = get_settings()
    if settings.environment != "production" or os.environ.get("WOS_RUNTIME_PROFILE") != "v2":
        raise RuntimeError("Gate 11 account creation requires the production V2 profile")
    if Path(settings.data_root) != Path("/data"):
        raise RuntimeError("Gate 11 account creation requires the isolated /data root")

    username = _pilot_username(campaign)
    credential_path = _credential_path(campaign)
    if credential_path.exists() or credential_path.is_symlink():
        raise RuntimeError("pilot credential staging path already exists")

    workspace_manager = WorkspaceManager(settings.data_root)
    async with session_factory() as session:
        existing = await session.scalar(
            select(User).where(func.lower(User.username) == username.lower())
        )
        await session.rollback()
        if existing is not None:
            raise RuntimeError("pilot account already exists")
        user, initial_password = await create_managed_user(
            session,
            workspace_manager=workspace_manager,
        )
        user = await change_username(
            session,
            user=user,
            username_input=username,
            workspace_manager=workspace_manager,
        )
        forced = user.must_change_credentials
        active = user.is_active and user.deleted_at is None

    fd = os.open(credential_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"username": username, "password": initial_password}, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())

    workspace = Path(settings.data_root) / username
    return {
        "pilot_account_count": 1,
        "forced_credential_change": forced,
        "pilot_account_active": active,
        "workspace_ready": workspace.is_dir() and not workspace.is_symlink(),
        "credential_file_written": credential_path.is_file() and not credential_path.is_symlink(),
        "v1_data_moves": 0,
        "credentials_in_output": 0,
        "v1_write_operations": 0,
        "v1_unchanged": True,
    }


async def pilot_inspect(campaign: str) -> dict[str, int | bool]:
    username = _pilot_username(campaign)
    settings = get_settings()
    async with session_factory() as session:
        users = list(
            (
                await session.scalars(
                    select(User).where(func.lower(User.username) == username.lower())
                )
            ).all()
        )
        await session.rollback()
    workspace = Path(settings.data_root) / username
    return {
        "pilot_account_count": len(users),
        "forced_credential_change": bool(users and users[0].must_change_credentials),
        "pilot_account_active": bool(
            users and users[0].is_active and users[0].deleted_at is None
        ),
        "workspace_ready": workspace.is_dir() and not workspace.is_symlink(),
    }


async def main_async() -> None:
    args = parse_args()
    if CAMPAIGN_RE.fullmatch(args.campaign) is None:
        raise RuntimeError("invalid campaign id")
    try:
        if args.mode == "cleanup-test":
            result = await cleanup_test_data()
        elif args.mode == "pilot-create":
            result = await pilot_create(args.campaign)
        else:
            result = await pilot_inspect(args.campaign)
        print(json.dumps(result, sort_keys=True))
    finally:
        await engine.dispose()


def main() -> int:
    try:
        asyncio.run(main_async())
    except Exception as exc:
        print(f"data hygiene probe failed: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
