"""Bounded retention for tables that would otherwise grow without limit.

Everything here deletes in short transactions of at most ``BATCH`` rows and stops after a
time budget, so a large backlog is absorbed over several passes without long locks.
Rows locked by another replica are skipped, which makes concurrent passes harmless.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Delete

from app.models import LoginThrottle, TorrentJob, TorrentJobState, UserSession

logger = logging.getLogger(__name__)

SYNC_JOB_TYPE = "SYNC_TORRENT"
# ADD jobs are never pruned: the latest one decides whether a failed torrent can be retried.
ADD_JOB_TYPE = "ADD_TORRENT"

FINISHED_SYNC_RETENTION = timedelta(hours=1)
FAILED_SYNC_RETENTION = timedelta(days=7)
OTHER_JOB_RETENTION = timedelta(days=180)
SESSION_RETENTION = timedelta(days=7)
THROTTLE_RETENTION = timedelta(days=1)
BATCH = 1000
TIME_BUDGET_SECONDS = 30.0

type Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class PruneResult:
    jobs: int = 0
    sessions: int = 0
    throttles: int = 0

    @property
    def total(self) -> int:
        return self.jobs + self.sessions + self.throttles


def _database_timestamp(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo is not None else value


def _finished_job_statement(now: datetime) -> Delete:
    cutoff = _database_timestamp(now)
    terminal = (TorrentJobState.COMPLETED, TorrentJobState.CANCELLED)
    condition = or_(
        and_(
            TorrentJob.job_type == SYNC_JOB_TYPE,
            TorrentJob.state.in_(terminal),
            TorrentJob.finished_at < cutoff - FINISHED_SYNC_RETENTION,
        ),
        and_(
            TorrentJob.job_type == SYNC_JOB_TYPE,
            TorrentJob.state == TorrentJobState.FAILED,
            TorrentJob.finished_at < cutoff - FAILED_SYNC_RETENTION,
        ),
        and_(
            TorrentJob.job_type.not_in((SYNC_JOB_TYPE, ADD_JOB_TYPE)),
            TorrentJob.state.in_((*terminal, TorrentJobState.FAILED)),
            TorrentJob.finished_at < cutoff - OTHER_JOB_RETENTION,
        ),
    )
    ids = select(TorrentJob.id).where(condition).limit(BATCH).with_for_update(skip_locked=True)
    return delete(TorrentJob).where(TorrentJob.id.in_(ids))


def _stale_session_statement(now: datetime) -> Delete:
    cutoff = now - SESSION_RETENTION
    ids = (
        select(UserSession.id)
        .where(or_(UserSession.expires_at < cutoff, UserSession.revoked_at < cutoff))
        .limit(BATCH)
        .with_for_update(skip_locked=True)
    )
    return delete(UserSession).where(UserSession.id.in_(ids))


def _stale_throttle_statement(now: datetime) -> Delete:
    ids = (
        select(LoginThrottle.key_hash)
        .where(
            LoginThrottle.window_started_at < now - THROTTLE_RETENTION,
            or_(LoginThrottle.locked_until.is_(None), LoginThrottle.locked_until < now),
        )
        .limit(BATCH)
        .with_for_update(skip_locked=True)
    )
    return delete(LoginThrottle).where(LoginThrottle.key_hash.in_(ids))


async def _delete_in_batches(
    session_factory: async_sessionmaker[AsyncSession],
    statement: Delete,
    *,
    deadline: float,
    monotonic: Clock,
) -> int:
    deleted = 0
    while True:
        async with session_factory() as session, session.begin():
            result = await session.execute(statement)
            count = result.rowcount if isinstance(result, CursorResult) else 0
        deleted += max(0, count)
        if count < BATCH or monotonic() >= deadline:
            return deleted


async def prune_stale_rows(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    budget_seconds: float = TIME_BUDGET_SECONDS,
    monotonic: Clock = time.monotonic,
) -> PruneResult:
    """Delete finished sync jobs, stale sessions and expired login throttles."""

    moment = now or datetime.now(UTC)
    deadline = monotonic() + budget_seconds
    jobs = await _delete_in_batches(
        session_factory, _finished_job_statement(moment), deadline=deadline, monotonic=monotonic
    )
    sessions = await _delete_in_batches(
        session_factory, _stale_session_statement(moment), deadline=deadline, monotonic=monotonic
    )
    throttles = await _delete_in_batches(
        session_factory, _stale_throttle_statement(moment), deadline=deadline, monotonic=monotonic
    )
    result = PruneResult(jobs=jobs, sessions=sessions, throttles=throttles)
    if result.total:
        logger.info(
            "maintenance_pruned_rows",
            extra={"jobs": jobs, "sessions": sessions, "throttles": throttles},
        )
    return result
