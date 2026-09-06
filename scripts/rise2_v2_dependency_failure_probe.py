#!/usr/bin/env python3
"""Create and inspect secret-free V2-33 dependency failure sentinels."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select

from app.core.database import engine, session_factory
from app.models import ManagedTorrent, ManagedTorrentState, TorrentJob, TorrentJobState

CAMPAIGN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,15}$")
SCENARIO_JOB_COUNT = 8
RECOVERY_JOB_TYPE = "V233_RECOVERY_CANARY"
SYNC_JOB_TYPE = "SYNC_TORRENT"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("setup", "snapshot", "cleanup"))
    parser.add_argument("--campaign", required=True)
    return parser.parse_args()


def prefix(campaign: str) -> str:
    return f"v233-dependency-{campaign}-"


def info_hash(campaign: str, index: int) -> str:
    return hashlib.sha1(f"v233:{campaign}:{index}".encode()).hexdigest()


async def setup(campaign: str) -> dict[str, int | bool]:
    name_prefix = prefix(campaign)
    now = datetime.now(UTC).replace(tzinfo=None)
    future = now + timedelta(hours=6)
    async with session_factory() as session, session.begin():
        existing = int(
            await session.scalar(
                select(func.count())
                .select_from(ManagedTorrent)
                .where(ManagedTorrent.name.like(f"{name_prefix}%"))
            )
            or 0
        )
        if existing:
            raise RuntimeError("dependency sentinel campaign already exists")

        for index in range(SCENARIO_JOB_COUNT):
            torrent = ManagedTorrent(
                info_hash=info_hash(campaign, index),
                name=f"{name_prefix}{index}",
                total_size=0,
                state=ManagedTorrentState.PURGED,
            )
            session.add(torrent)
            await session.flush()
            session.add(
                TorrentJob(
                    managed_torrent_id=torrent.id,
                    job_type=SYNC_JOB_TYPE,
                    idempotency_key=f"v233-dependency:{campaign}:{index}",
                    state=TorrentJobState.QUEUED,
                    available_at=future,
                    max_attempts=3,
                )
            )

        recovery_torrent = ManagedTorrent(
            info_hash=info_hash(campaign, SCENARIO_JOB_COUNT),
            name=f"{name_prefix}recovery",
            total_size=0,
            state=ManagedTorrentState.PURGED,
        )
        session.add(recovery_torrent)
        await session.flush()
        session.add(
            TorrentJob(
                managed_torrent_id=recovery_torrent.id,
                job_type=RECOVERY_JOB_TYPE,
                idempotency_key=f"v233-dependency:{campaign}:recovery",
                state=TorrentJobState.RUNNING,
                attempt_count=1,
                max_attempts=3,
                available_at=now - timedelta(minutes=1),
                claimed_by="v233-recovery",
                claim_expires_at=now - timedelta(minutes=1),
                timeout_at=now + timedelta(minutes=10),
            )
        )

    return {
        "sentinel_torrents": SCENARIO_JOB_COUNT + 1,
        "sentinel_jobs": SCENARIO_JOB_COUNT + 1,
        "recovery_canary_created": True,
    }


async def snapshot(campaign: str) -> dict[str, int | bool]:
    name_prefix = prefix(campaign)
    now = datetime.now(UTC).replace(tzinfo=None)
    async with session_factory() as session:
        torrent_ids = select(ManagedTorrent.id).where(
            ManagedTorrent.name.like(f"{name_prefix}%")
        )
        torrents = int(
            await session.scalar(
                select(func.count())
                .select_from(ManagedTorrent)
                .where(ManagedTorrent.name.like(f"{name_prefix}%"))
            )
            or 0
        )
        jobs = int(
            await session.scalar(
                select(func.count())
                .select_from(TorrentJob)
                .where(TorrentJob.managed_torrent_id.in_(torrent_ids))
            )
            or 0
        )
        queued_jobs = int(
            await session.scalar(
                select(func.count())
                .select_from(TorrentJob)
                .where(
                    TorrentJob.managed_torrent_id.in_(torrent_ids),
                    TorrentJob.state == TorrentJobState.QUEUED,
                )
            )
            or 0
        )
        running_jobs = int(
            await session.scalar(
                select(func.count())
                .select_from(TorrentJob)
                .where(
                    TorrentJob.managed_torrent_id.in_(torrent_ids),
                    TorrentJob.state == TorrentJobState.RUNNING,
                )
            )
            or 0
        )
        future_sync_jobs = int(
            await session.scalar(
                select(func.count())
                .select_from(TorrentJob)
                .where(
                    TorrentJob.managed_torrent_id.in_(torrent_ids),
                    TorrentJob.job_type == SYNC_JOB_TYPE,
                    TorrentJob.state == TorrentJobState.QUEUED,
                    TorrentJob.available_at > now,
                )
            )
            or 0
        )
        recovery = await session.scalar(
            select(TorrentJob).where(
                TorrentJob.managed_torrent_id.in_(torrent_ids),
                TorrentJob.job_type == RECOVERY_JOB_TYPE,
            )
        )

    return {
        "sentinel_torrents": torrents,
        "sentinel_jobs": jobs,
        "queued_jobs": queued_jobs,
        "running_jobs": running_jobs,
        "future_sync_jobs": future_sync_jobs,
        "recovery_canary_queued": bool(
            recovery is not None and recovery.state is TorrentJobState.QUEUED
        ),
        "recovery_canary_unclaimed": bool(
            recovery is not None
            and recovery.claimed_by is None
            and recovery.claim_expires_at is None
            and recovery.timeout_at is None
        ),
        "recovery_canary_backoff": bool(
            recovery is not None and recovery.available_at > now
        ),
        "recovery_canary_attempt_count": recovery.attempt_count if recovery is not None else -1,
        "recovery_canary_error_safe": bool(
            recovery is not None and recovery.last_error_code == "claim_expired"
        ),
    }


async def cleanup(campaign: str) -> dict[str, int | bool]:
    name_prefix = prefix(campaign)
    async with session_factory() as session, session.begin():
        result = await session.execute(
            delete(ManagedTorrent).where(ManagedTorrent.name.like(f"{name_prefix}%"))
        )
        deleted = int(result.rowcount or 0)
    remaining = await snapshot(campaign)
    if remaining["sentinel_torrents"] or remaining["sentinel_jobs"]:
        raise RuntimeError("dependency sentinel cleanup left residual rows")
    return {
        "deleted_torrents": deleted,
        "remaining_torrents": int(remaining["sentinel_torrents"]),
        "remaining_jobs": int(remaining["sentinel_jobs"]),
    }


async def main_async() -> None:
    args = parse_args()
    if CAMPAIGN_RE.fullmatch(args.campaign) is None:
        raise RuntimeError("invalid campaign id")
    try:
        if args.mode == "setup":
            result = await setup(args.campaign)
        elif args.mode == "snapshot":
            result = await snapshot(args.campaign)
        else:
            result = await cleanup(args.campaign)
        print(json.dumps(result, sort_keys=True))
    finally:
        await engine.dispose()


def main() -> int:
    try:
        asyncio.run(main_async())
    except Exception as exc:
        print(f"dependency failure probe failed: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
