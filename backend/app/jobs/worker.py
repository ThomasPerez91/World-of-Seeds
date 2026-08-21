from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination import RedisCoordinator
from app.jobs.torrent_jobs import (
    TorrentJobTransitionError,
    cancel_claimed_torrent_job,
    claim_next_torrent_job,
    complete_torrent_job,
    fail_torrent_job,
    recover_expired_torrent_jobs,
    renew_torrent_job_claim,
    retry_torrent_job,
)
from app.models import TorrentJob, TorrentJobState

logger = logging.getLogger(__name__)

type TorrentJobHandler = Callable[[TorrentJobSnapshot], Coroutine[Any, Any, None]]
type Clock = Callable[[], datetime]
type Jitter = Callable[[float, float], float]


class TransientTorrentJobError(RuntimeError):
    """A retryable worker failure represented by a bounded, secret-safe code."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class PermanentTorrentJobError(RuntimeError):
    """A permanent worker failure represented by a bounded, secret-safe code."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class TorrentJobClaimLostError(RuntimeError):
    """Raised when the worker can no longer safely own an external effect."""


@dataclass(frozen=True, slots=True)
class TorrentJobSnapshot:
    id: uuid.UUID
    managed_torrent_id: uuid.UUID
    torrent_request_id: uuid.UUID | None
    job_type: str
    idempotency_key: str
    attempt_count: int

    @classmethod
    def from_model(cls, job: TorrentJob) -> TorrentJobSnapshot:
        return cls(
            id=job.id,
            managed_torrent_id=job.managed_torrent_id,
            torrent_request_id=job.torrent_request_id,
            job_type=job.job_type,
            idempotency_key=job.idempotency_key,
            attempt_count=job.attempt_count,
        )


@dataclass(frozen=True, slots=True)
class TorrentWorkerConfig:
    concurrency: int = 1
    poll_interval: timedelta = timedelta(seconds=5)
    claim_ttl: timedelta = timedelta(seconds=60)
    execution_timeout: timedelta = timedelta(minutes=10)
    recovery_interval: timedelta = timedelta(seconds=30)
    retry_base: timedelta = timedelta(seconds=30)
    retry_max: timedelta = timedelta(hours=1)
    shutdown_grace: timedelta = timedelta(seconds=30)

    def __post_init__(self) -> None:
        durations = (
            self.poll_interval,
            self.claim_ttl,
            self.execution_timeout,
            self.recovery_interval,
            self.retry_base,
            self.retry_max,
            self.shutdown_grace,
        )
        if not 1 <= self.concurrency <= 16:
            raise ValueError("worker concurrency must be between 1 and 16")
        if any(duration <= timedelta(0) for duration in durations):
            raise ValueError("worker durations must be positive")
        if self.claim_ttl > self.execution_timeout:
            raise ValueError("claim_ttl cannot exceed execution_timeout")
        if self.retry_base > self.retry_max:
            raise ValueError("retry_base cannot exceed retry_max")


class TorrentWorker:
    """Durable PostgreSQL worker; Redis only shortens the polling delay."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis: RedisCoordinator,
        handlers: Mapping[str, TorrentJobHandler],
        *,
        worker_id: str,
        config: TorrentWorkerConfig | None = None,
        clock: Clock = lambda: datetime.now(UTC),
        jitter: Jitter = random.uniform,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis
        self._handlers = dict(handlers)
        self._worker_id = worker_id
        self._config = config or TorrentWorkerConfig()
        self._clock = clock
        self._jitter = jitter
        self._stop = asyncio.Event()
        self._active: set[asyncio.Task[None]] = set()
        self._last_recovery: datetime | None = None

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        logger.info("torrent_worker_started", extra={"worker_id": self._worker_id})
        try:
            while not self._stop.is_set():
                await self._recover_if_due()
                self._discard_finished_tasks()
                await self._fill_capacity()
                await self._wait_for_wakeup()
        finally:
            await self._drain_active_tasks()
            logger.info("torrent_worker_stopped", extra={"worker_id": self._worker_id})

    async def process_once(self) -> bool:
        """Claim and execute at most one supported job; useful for bounded invocations/tests."""

        await self._recover_if_due(force=True)
        job = await self._claim_one()
        if job is None:
            return False
        await self._process_claimed_job(TorrentJobSnapshot.from_model(job))
        return True

    async def _recover_if_due(self, *, force: bool = False) -> None:
        now = self._clock()
        if (
            not force
            and self._last_recovery is not None
            and now - self._last_recovery < self._config.recovery_interval
        ):
            return
        try:
            async with self._session_factory() as session, session.begin():
                recovered = await recover_expired_torrent_jobs(
                    session,
                    now=now,
                    retry_delay=self._config.retry_base,
                )
            self._last_recovery = now
            if recovered:
                logger.info("torrent_worker_recovered_jobs", extra={"count": len(recovered)})
        except SQLAlchemyError:
            logger.warning("torrent_worker_database_unavailable")

    async def _claim_one(self) -> TorrentJob | None:
        if not self._handlers:
            return None
        try:
            async with self._session_factory() as session, session.begin():
                job = await claim_next_torrent_job(
                    session,
                    worker_id=self._worker_id,
                    now=self._clock(),
                    claim_ttl=self._config.claim_ttl,
                    execution_timeout=self._config.execution_timeout,
                    job_types=self._handlers,
                )
            return job
        except SQLAlchemyError:
            logger.warning("torrent_worker_database_unavailable")
            return None

    async def _fill_capacity(self) -> None:
        while len(self._active) < self._config.concurrency and not self._stop.is_set():
            job = await self._claim_one()
            if job is None:
                break
            task = asyncio.create_task(
                self._process_claimed_job(TorrentJobSnapshot.from_model(job)),
                name=f"torrent-job-{job.id}",
            )
            self._active.add(task)

    def _discard_finished_tasks(self) -> None:
        finished = {task for task in self._active if task.done()}
        self._active.difference_update(finished)
        for task in finished:
            if not task.cancelled() and task.exception() is not None:
                logger.error("torrent_worker_task_failed")

    async def _process_claimed_job(self, snapshot: TorrentJobSnapshot) -> None:
        handler = self._handlers[snapshot.job_type]
        heartbeat_stop = asyncio.Event()
        handler_task = asyncio.create_task(handler(snapshot))
        heartbeat_task = asyncio.create_task(self._heartbeat(snapshot.id, heartbeat_stop))
        try:
            done, _ = await asyncio.wait(
                {handler_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                exception = heartbeat_task.exception()
                if exception is not None:
                    handler_task.cancel()
                    await asyncio.gather(handler_task, return_exceptions=True)
                    logger.warning(
                        "torrent_worker_claim_lost",
                        extra={"cause_type": type(exception).__name__},
                    )
                    return

            try:
                await handler_task
            except TransientTorrentJobError as exc:
                await self._finish_failure(snapshot, exc.error_code, permanent=False)
            except PermanentTorrentJobError as exc:
                await self._finish_failure(snapshot, exc.error_code, permanent=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("torrent_worker_handler_failed")
                await self._finish_failure(snapshot, "worker_unexpected_error", permanent=False)
            else:
                await self._finish_success(snapshot)
        finally:
            heartbeat_stop.set()
            if not handler_task.done():
                handler_task.cancel()
            if not heartbeat_task.done():
                heartbeat_task.cancel()
            await asyncio.gather(handler_task, heartbeat_task, return_exceptions=True)

    async def _heartbeat(self, job_id: uuid.UUID, stop: asyncio.Event) -> None:
        interval = self._config.claim_ttl.total_seconds() / 3
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            try:
                async with self._session_factory() as session, session.begin():
                    job = await session.scalar(
                        select(TorrentJob).where(TorrentJob.id == job_id).with_for_update()
                    )
                    if job is None:
                        raise TorrentJobClaimLostError("job no longer exists")
                    await renew_torrent_job_claim(
                        session,
                        job,
                        worker_id=self._worker_id,
                        now=self._clock(),
                        claim_ttl=self._config.claim_ttl,
                    )
            except (SQLAlchemyError, TorrentJobTransitionError) as exc:
                raise TorrentJobClaimLostError("claim could not be renewed") from exc

    async def _finish_success(self, snapshot: TorrentJobSnapshot) -> None:
        async with self._session_factory() as session, session.begin():
            job = await self._owned_job(session, snapshot.id)
            if job.cancel_requested_at is not None:
                await cancel_claimed_torrent_job(
                    session, job, worker_id=self._worker_id, now=self._clock()
                )
            else:
                await complete_torrent_job(
                    session, job, worker_id=self._worker_id, now=self._clock()
                )

    async def _finish_failure(
        self,
        snapshot: TorrentJobSnapshot,
        error_code: str,
        *,
        permanent: bool,
    ) -> None:
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            job = await self._owned_job(session, snapshot.id)
            if job.cancel_requested_at is not None:
                await cancel_claimed_torrent_job(session, job, worker_id=self._worker_id, now=now)
            elif permanent:
                await fail_torrent_job(
                    session,
                    job,
                    worker_id=self._worker_id,
                    now=now,
                    error_code=error_code,
                )
            else:
                base = self._config.retry_base.total_seconds()
                maximum = self._config.retry_max.total_seconds()
                upper = min(maximum, base * (2 ** max(0, snapshot.attempt_count - 1)))
                delay = self._jitter(base, upper)
                await retry_torrent_job(
                    session,
                    job,
                    worker_id=self._worker_id,
                    now=now,
                    available_at=now + timedelta(seconds=delay),
                    error_code=error_code,
                )

    async def _owned_job(self, session: AsyncSession, job_id: uuid.UUID) -> TorrentJob:
        job = await session.scalar(
            select(TorrentJob).where(TorrentJob.id == job_id).with_for_update()
        )
        if (
            job is None
            or job.state is not TorrentJobState.RUNNING
            or job.claimed_by != self._worker_id
        ):
            raise TorrentJobClaimLostError("worker no longer owns the job")
        return job

    async def _wait_for_wakeup(self) -> None:
        timeout = self._config.poll_interval.total_seconds()
        started_at = asyncio.get_running_loop().time()
        signal_task = asyncio.create_task(self._redis.wait_for_job_signal(timeout_seconds=timeout))
        stop_task = asyncio.create_task(self._stop.wait())
        active_tasks = set(self._active)
        try:
            done, _ = await asyncio.wait(
                {signal_task, stop_task, *active_tasks},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if signal_task in done and not signal_task.result() and not self._stop.is_set():
                elapsed = asyncio.get_running_loop().time() - started_at
                await asyncio.wait(
                    {stop_task, *active_tasks},
                    timeout=max(0, timeout - elapsed),
                    return_when=asyncio.FIRST_COMPLETED,
                )
        finally:
            for task in (signal_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(signal_task, stop_task, return_exceptions=True)

    async def _drain_active_tasks(self) -> None:
        if not self._active:
            return
        _, pending = await asyncio.wait(
            self._active,
            timeout=self._config.shutdown_grace.total_seconds(),
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
