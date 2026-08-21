from __future__ import annotations

import re
import uuid
from collections.abc import Collection
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TorrentJob, TorrentJobState

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_.-]+$")
_SAFE_JOB_TYPE = re.compile(r"^[A-Z0-9_]+$")


class TorrentJobTransitionError(ValueError):
    """Raised when a durable job transition would violate the V2 state machine."""


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _database_timestamp(value: datetime) -> datetime:
    """Bind UTC consistently to the existing timezone-naive SQL timestamp columns."""

    return _as_utc(value).replace(tzinfo=None)


def _validate_worker_id(worker_id: str) -> None:
    if not 1 <= len(worker_id) <= 128 or _SAFE_IDENTIFIER.fullmatch(worker_id) is None:
        raise ValueError("worker_id must be a safe opaque identifier")


def _validate_error_code(error_code: str) -> None:
    if not 1 <= len(error_code) <= 64 or _SAFE_ERROR_CODE.fullmatch(error_code) is None:
        raise ValueError("error_code must be a safe diagnostic code")


def _validate_job_type(job_type: str) -> None:
    if not 1 <= len(job_type) <= 64 or _SAFE_JOB_TYPE.fullmatch(job_type) is None:
        raise ValueError("job_type must be a safe opaque identifier")


def _require_claim_owner(job: TorrentJob, worker_id: str) -> None:
    _validate_worker_id(worker_id)
    if job.state is not TorrentJobState.RUNNING or job.claimed_by != worker_id:
        raise TorrentJobTransitionError("the worker does not own this running job")


def _release_claim(job: TorrentJob) -> None:
    job.claimed_by = None
    job.claim_expires_at = None
    job.timeout_at = None


def _finish(job: TorrentJob, state: TorrentJobState, now: datetime) -> None:
    job.state = state
    job.finished_at = now
    _release_claim(job)


async def claim_next_torrent_job(
    session: AsyncSession,
    *,
    worker_id: str,
    now: datetime,
    claim_ttl: timedelta,
    execution_timeout: timedelta,
    job_types: Collection[str] | None = None,
) -> TorrentJob | None:
    """Claim the oldest available job without waiting on another worker's row lock."""

    _validate_worker_id(worker_id)
    if claim_ttl <= timedelta(0) or execution_timeout <= timedelta(0):
        raise ValueError("claim and execution timeouts must be positive")

    accepted_job_types: tuple[str, ...] | None = None
    if job_types is not None:
        accepted_job_types = tuple(sorted(set(job_types)))
        for job_type in accepted_job_types:
            _validate_job_type(job_type)
        if not accepted_job_types:
            return None

    statement = select(TorrentJob).where(
        TorrentJob.state == TorrentJobState.QUEUED,
        TorrentJob.available_at <= _database_timestamp(now),
        TorrentJob.attempt_count < TorrentJob.max_attempts,
    )
    if accepted_job_types is not None:
        statement = statement.where(TorrentJob.job_type.in_(accepted_job_types))

    job = await session.scalar(
        statement.order_by(TorrentJob.available_at, TorrentJob.created_at, TorrentJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None

    job.state = TorrentJobState.RUNNING
    job.attempt_count += 1
    job.claimed_by = worker_id
    job.claim_expires_at = now + claim_ttl
    job.timeout_at = now + execution_timeout
    job.updated_at = now
    await session.flush()
    return job


async def complete_torrent_job(
    session: AsyncSession,
    job: TorrentJob,
    *,
    worker_id: str,
    now: datetime,
) -> None:
    _require_claim_owner(job, worker_id)
    final_state = (
        TorrentJobState.CANCELLED
        if job.cancel_requested_at is not None
        else TorrentJobState.COMPLETED
    )
    _finish(job, final_state, now)
    job.updated_at = now
    await session.flush()


async def renew_torrent_job_claim(
    session: AsyncSession,
    job: TorrentJob,
    *,
    worker_id: str,
    now: datetime,
    claim_ttl: timedelta,
) -> None:
    _require_claim_owner(job, worker_id)
    if claim_ttl <= timedelta(0):
        raise ValueError("claim_ttl must be positive")
    if job.timeout_at is None or _as_utc(now) >= _as_utc(job.timeout_at):
        raise TorrentJobTransitionError("the job execution timeout has expired")
    renewed_until = now + claim_ttl
    job.claim_expires_at = (
        renewed_until if _as_utc(renewed_until) <= _as_utc(job.timeout_at) else job.timeout_at
    )
    job.updated_at = now
    await session.flush()


async def retry_torrent_job(
    session: AsyncSession,
    job: TorrentJob,
    *,
    worker_id: str,
    now: datetime,
    available_at: datetime,
    error_code: str,
) -> None:
    _require_claim_owner(job, worker_id)
    _validate_error_code(error_code)
    if _as_utc(available_at) < _as_utc(now):
        raise ValueError("available_at cannot be in the past")

    job.last_error_code = error_code
    job.updated_at = now
    if job.attempt_count >= job.max_attempts:
        _finish(job, TorrentJobState.FAILED, now)
    else:
        job.state = TorrentJobState.QUEUED
        job.available_at = available_at
        _release_claim(job)
    await session.flush()


async def fail_torrent_job(
    session: AsyncSession,
    job: TorrentJob,
    *,
    worker_id: str,
    now: datetime,
    error_code: str,
) -> None:
    """Finish an owned job after a permanent, secret-safe failure."""

    _require_claim_owner(job, worker_id)
    _validate_error_code(error_code)
    job.last_error_code = error_code
    job.updated_at = now
    _finish(job, TorrentJobState.FAILED, now)
    await session.flush()


async def request_torrent_job_cancellation(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    now: datetime,
) -> TorrentJob | None:
    job = await session.scalar(select(TorrentJob).where(TorrentJob.id == job_id).with_for_update())
    if job is None:
        return None
    if job.state is TorrentJobState.QUEUED:
        job.cancel_requested_at = now
        _finish(job, TorrentJobState.CANCELLED, now)
    elif job.state is TorrentJobState.RUNNING and job.cancel_requested_at is None:
        job.cancel_requested_at = now
    job.updated_at = now
    await session.flush()
    return job


async def cancel_claimed_torrent_job(
    session: AsyncSession,
    job: TorrentJob,
    *,
    worker_id: str,
    now: datetime,
) -> None:
    _require_claim_owner(job, worker_id)
    if job.cancel_requested_at is None:
        raise TorrentJobTransitionError("cancellation was not requested")
    _finish(job, TorrentJobState.CANCELLED, now)
    job.updated_at = now
    await session.flush()


async def recover_expired_torrent_jobs(
    session: AsyncSession,
    *,
    now: datetime,
    retry_delay: timedelta,
    limit: int = 100,
) -> list[TorrentJob]:
    """Recover abandoned claims for replay or fail them when attempts are exhausted."""

    if retry_delay < timedelta(0):
        raise ValueError("retry_delay cannot be negative")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    jobs = list(
        (
            await session.scalars(
                select(TorrentJob)
                .where(
                    TorrentJob.state == TorrentJobState.RUNNING,
                    or_(
                        TorrentJob.claim_expires_at <= _database_timestamp(now),
                        TorrentJob.timeout_at <= _database_timestamp(now),
                    ),
                )
                .order_by(TorrentJob.claim_expires_at, TorrentJob.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        ).all()
    )
    for job in jobs:
        job.last_error_code = (
            "execution_timeout"
            if job.timeout_at is not None and _as_utc(job.timeout_at) <= _as_utc(now)
            else "claim_expired"
        )
        job.updated_at = now
        if job.attempt_count >= job.max_attempts:
            _finish(job, TorrentJobState.FAILED, now)
        else:
            job.state = TorrentJobState.QUEUED
            job.available_at = now + retry_delay
            _release_claim(job)
    await session.flush()
    return jobs
