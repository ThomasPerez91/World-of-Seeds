#!/usr/bin/env python3
"""Bounded secret-safe resource and disk-admission probe for V2-33 Gate 8."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from sqlalchemy import delete, func, select

from app.auth.security import DUMMY_PASSWORD_HASH
from app.core.config import get_settings
from app.core.database import engine, session_factory
from app.models import ManagedTorrent, TorrentRequest, User
from app.options import PostgresOptionsRegistry
from app.storage.accounting import (
    StorageAdmissionError,
    StorageAdmissionPolicy,
    StorageDiskSnapshot,
)
from app.torrents.deduplication import create_or_get_torrent_request

CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")
RAM_BYTES = 64 * 1024 * 1024
IO_BYTES = 64 * 1024 * 1024
IO_CHUNK_BYTES = 1024 * 1024
CPU_SECONDS = 3.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("workload", "admission", "cleanup"))
    parser.add_argument("--campaign", required=True)
    return parser.parse_args()


def _username(campaign: str) -> str:
    return f"resource-{campaign}"


def _torrent_name(campaign: str) -> str:
    return f"v233-resource-{campaign}"


def _info_hash(campaign: str) -> str:
    return hashlib.sha1(
        f"v233-resource:{campaign}".encode(), usedforsecurity=False
    ).hexdigest()


def workload(campaign: str) -> dict[str, int | float | bool]:
    settings = get_settings()
    if settings.environment != "production" or os.environ.get("WOS_RUNTIME_PROFILE") != "v2":
        raise RuntimeError("Gate 8 workload requires the production V2 profile")
    if Path(settings.data_root) != Path("/data"):
        raise RuntimeError("Gate 8 workload requires the isolated /data root")

    target = Path(settings.data_root) / f".v233-resource-{campaign}.bin"
    if target.exists() or target.is_symlink():
        raise RuntimeError("Gate 8 workload target already exists")

    disk_before = shutil.disk_usage(settings.data_root).free
    memory = bytearray(RAM_BYTES)
    memory[0] = 1
    memory[-1] = 1

    cpu_started = time.perf_counter()
    iterations = 0
    payload = b"world-of-seeds-v2-resource-pressure"
    while time.perf_counter() - cpu_started < CPU_SECONDS:
        payload = hashlib.sha256(payload).digest()
        iterations += 1
    cpu_elapsed = time.perf_counter() - cpu_started

    chunk = hashlib.sha256(b"wos-v233-io").digest() * (IO_CHUNK_BYTES // 32)
    written = 0
    try:
        with target.open("xb") as stream:
            while written < IO_BYTES:
                stream.write(chunk)
                written += len(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        disk_during = shutil.disk_usage(settings.data_root).free
    finally:
        target.unlink(missing_ok=True)

    disk_after = shutil.disk_usage(settings.data_root).free
    del memory
    return {
        "cpu_iterations": iterations,
        "cpu_seconds": round(cpu_elapsed, 3),
        "ram_bytes": RAM_BYTES,
        "io_bytes": written,
        "disk_free_before_bytes": disk_before,
        "disk_free_during_bytes": disk_during,
        "disk_free_after_bytes": disk_after,
        "workload_file_cleaned": not target.exists(),
    }


async def admission(campaign: str) -> dict[str, int | bool]:
    username = _username(campaign)
    info_hash = _info_hash(campaign)
    total_size = 1024 * 1024
    user_id = None
    rejection_code = ""
    false_successes = 0

    async with session_factory() as session:
        existing = int(
            await session.scalar(select(func.count(User.id)).where(User.username == username)) or 0
        )
        await session.rollback()
        if existing:
            raise RuntimeError("Gate 8 admission user already exists")

        user = User(
            username=username,
            password_hash=DUMMY_PASSWORD_HASH,
            is_active=True,
            must_change_credentials=False,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

        try:
            options = await PostgresOptionsRegistry().snapshot(session)
            policy = StorageAdmissionPolicy.from_options(options)
            await session.rollback()
            critical_disk = StorageDiskSnapshot(total_bytes=100 * 1024**3, free_bytes=0)
            try:
                async with session.begin():
                    await create_or_get_torrent_request(
                        session,
                        user_id=user_id,
                        info_hash=info_hash,
                        name=_torrent_name(campaign),
                        total_size=total_size,
                        storage_policy=policy,
                        disk_snapshot=critical_disk,
                    )
                false_successes = 1
            except StorageAdmissionError as exc:
                rejection_code = exc.code
                await session.rollback()

            residual_torrents = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ManagedTorrent)
                    .where(ManagedTorrent.info_hash == info_hash)
                )
                or 0
            )
            residual_requests = int(
                await session.scalar(
                    select(func.count())
                    .select_from(TorrentRequest)
                    .where(TorrentRequest.user_id == user_id)
                )
                or 0
            )
            await session.rollback()
        finally:
            if user_id is not None:
                async with session.begin():
                    await session.execute(delete(User).where(User.id == user_id))

    return {
        "admission_failures": int(
            false_successes != 0
            or rejection_code != "disk_pressure_critical"
            or residual_torrents != 0
            or residual_requests != 0
        ),
        "false_successes": false_successes,
        "disk_pressure_fail_closed": rejection_code == "disk_pressure_critical",
        "residual_torrents": residual_torrents,
        "residual_requests": residual_requests,
    }


async def cleanup(campaign: str) -> dict[str, int | bool]:
    settings = get_settings()
    target = Path(settings.data_root) / f".v233-resource-{campaign}.bin"
    target.unlink(missing_ok=True)
    username = _username(campaign)
    info_hash = _info_hash(campaign)
    async with session_factory() as session, session.begin():
        await session.execute(delete(User).where(User.username == username))
        await session.execute(delete(ManagedTorrent).where(ManagedTorrent.info_hash == info_hash))
    return {"cleanup_complete": not target.exists()}


async def main_async() -> None:
    args = parse_args()
    if CAMPAIGN_RE.fullmatch(args.campaign) is None:
        raise RuntimeError("invalid campaign id")
    if args.mode == "workload":
        result = workload(args.campaign)
    elif args.mode == "admission":
        result = await admission(args.campaign)
    else:
        result = await cleanup(args.campaign)
    print(json.dumps(result, sort_keys=True))


def main() -> int:
    try:
        asyncio.run(main_async())
    except Exception as exc:
        print(f"resource pressure probe failed: {type(exc).__name__}")
        return 1
    finally:
        try:
            asyncio.run(engine.dispose())
        except RuntimeError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
