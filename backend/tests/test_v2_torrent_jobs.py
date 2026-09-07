import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.jobs import (
    TorrentJobTransitionError,
    cancel_claimed_torrent_job,
    claim_next_torrent_job,
    complete_torrent_job,
    recover_expired_torrent_jobs,
    renew_torrent_job_claim,
    request_torrent_job_cancellation,
    retry_torrent_job,
)
from app.models import ManagedTorrent, TorrentJob, TorrentJobState

NOW = datetime(2026, 8, 21, 12, tzinfo=UTC)
CLAIM_TTL = timedelta(minutes=2)
EXECUTION_TIMEOUT = timedelta(minutes=10)


def assert_job_state(job: TorrentJob, expected: TorrentJobState) -> None:
    assert job.state is expected


async def create_torrent(db_session: AsyncSession, info_hash: str = "b" * 40) -> ManagedTorrent:
    torrent = ManagedTorrent(info_hash=info_hash, name="Job torrent", total_size=100)
    db_session.add(torrent)
    await db_session.flush()
    return torrent


async def create_job(
    db_session: AsyncSession,
    torrent: ManagedTorrent,
    *,
    key: str,
    available_at: datetime = NOW,
    max_attempts: int = 3,
) -> TorrentJob:
    job = TorrentJob(
        managed_torrent=torrent,
        job_type="ADD_TORRENT",
        idempotency_key=key,
        available_at=available_at,
        max_attempts=max_attempts,
    )
    db_session.add(job)
    await db_session.flush()
    return job


def test_torrent_job_states_match_the_normative_state_machine() -> None:
    assert {state.value for state in TorrentJobState} == {
        "QUEUED",
        "RUNNING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }


@pytest.mark.asyncio
async def test_idempotency_key_is_unique(db_session: AsyncSession) -> None:
    torrent = await create_torrent(db_session)
    await create_job(db_session, torrent, key="same-effect")
    await db_session.commit()

    db_session.add(
        TorrentJob(
            managed_torrent_id=torrent.id,
            job_type="ADD_TORRENT",
            idempotency_key="same-effect",
            available_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


@pytest.mark.asyncio
async def test_claim_selects_oldest_available_job_and_sets_deadlines(
    db_session: AsyncSession,
) -> None:
    torrent = await create_torrent(db_session)
    oldest = await create_job(db_session, torrent, key="oldest", available_at=NOW)
    await create_job(db_session, torrent, key="future", available_at=NOW + timedelta(hours=1))

    claimed = await claim_next_torrent_job(
        db_session,
        worker_id="worker-1",
        now=NOW,
        claim_ttl=CLAIM_TTL,
        execution_timeout=EXECUTION_TIMEOUT,
    )

    assert claimed is oldest
    assert claimed.state is TorrentJobState.RUNNING
    assert claimed.attempt_count == 1
    assert claimed.claimed_by == "worker-1"
    assert claimed.claim_expires_at == NOW + CLAIM_TTL
    assert claimed.timeout_at == NOW + EXECUTION_TIMEOUT


@pytest.mark.asyncio
async def test_only_claim_owner_can_complete_job(db_session: AsyncSession) -> None:
    torrent = await create_torrent(db_session)
    job = await create_job(db_session, torrent, key="complete")
    await claim_next_torrent_job(
        db_session,
        worker_id="worker-1",
        now=NOW,
        claim_ttl=CLAIM_TTL,
        execution_timeout=EXECUTION_TIMEOUT,
    )

    with pytest.raises(TorrentJobTransitionError):
        await complete_torrent_job(db_session, job, worker_id="worker-2", now=NOW)

    await complete_torrent_job(db_session, job, worker_id="worker-1", now=NOW)
    assert job.state is TorrentJobState.COMPLETED
    assert job.finished_at == NOW
    assert job.claimed_by is None


@pytest.mark.asyncio
async def test_claim_renewal_is_owned_and_bounded_by_execution_timeout(
    db_session: AsyncSession,
) -> None:
    torrent = await create_torrent(db_session)
    job = await create_job(db_session, torrent, key="renew")
    await claim_next_torrent_job(
        db_session,
        worker_id="worker-1",
        now=NOW,
        claim_ttl=CLAIM_TTL,
        execution_timeout=EXECUTION_TIMEOUT,
    )

    with pytest.raises(TorrentJobTransitionError):
        await renew_torrent_job_claim(
            db_session,
            job,
            worker_id="worker-2",
            now=NOW + timedelta(minutes=1),
            claim_ttl=timedelta(minutes=20),
        )

    await renew_torrent_job_claim(
        db_session,
        job,
        worker_id="worker-1",
        now=NOW + timedelta(minutes=1),
        claim_ttl=timedelta(minutes=20),
    )
    assert job.claim_expires_at == NOW + EXECUTION_TIMEOUT


@pytest.mark.asyncio
async def test_retry_uses_backoff_then_fails_when_attempts_are_exhausted(
    db_session: AsyncSession,
) -> None:
    torrent = await create_torrent(db_session)
    job = await create_job(db_session, torrent, key="retry", max_attempts=2)
    await claim_next_torrent_job(
        db_session,
        worker_id="worker-1",
        now=NOW,
        claim_ttl=CLAIM_TTL,
        execution_timeout=EXECUTION_TIMEOUT,
    )

    retry_at = NOW + timedelta(minutes=5)
    await retry_torrent_job(
        db_session,
        job,
        worker_id="worker-1",
        now=NOW,
        available_at=retry_at,
        error_code="integration_unavailable",
    )
    assert job.state is TorrentJobState.QUEUED
    assert job.available_at == retry_at
    assert job.last_error_code == "integration_unavailable"

    await claim_next_torrent_job(
        db_session,
        worker_id="worker-2",
        now=retry_at,
        claim_ttl=CLAIM_TTL,
        execution_timeout=EXECUTION_TIMEOUT,
    )
    await retry_torrent_job(
        db_session,
        job,
        worker_id="worker-2",
        now=retry_at,
        available_at=retry_at + timedelta(minutes=5),
        error_code="integration_unavailable",
    )
    assert_job_state(job, TorrentJobState.FAILED)
    assert job.finished_at == retry_at


@pytest.mark.asyncio
async def test_recovery_requeues_abandoned_claim_and_fails_exhausted_job(
    db_session: AsyncSession,
) -> None:
    torrent = await create_torrent(db_session)
    replay = TorrentJob(
        managed_torrent=torrent,
        job_type="ADD_TORRENT",
        idempotency_key="replay",
        state=TorrentJobState.RUNNING,
        attempt_count=1,
        max_attempts=3,
        available_at=NOW - timedelta(minutes=10),
        claimed_by="dead-worker",
        claim_expires_at=NOW - timedelta(minutes=1),
        timeout_at=NOW + timedelta(minutes=1),
    )
    exhausted = TorrentJob(
        managed_torrent=torrent,
        job_type="ADD_TORRENT",
        idempotency_key="exhausted",
        state=TorrentJobState.RUNNING,
        attempt_count=1,
        max_attempts=1,
        available_at=NOW - timedelta(minutes=10),
        claimed_by="dead-worker",
        claim_expires_at=NOW + timedelta(minutes=1),
        timeout_at=NOW - timedelta(seconds=1),
    )
    db_session.add_all([replay, exhausted])
    await db_session.flush()

    recovered = await recover_expired_torrent_jobs(
        db_session,
        now=NOW,
        retry_delay=timedelta(minutes=3),
    )

    assert set(recovered) == {replay, exhausted}
    assert replay.state is TorrentJobState.QUEUED
    assert replay.available_at == NOW + timedelta(minutes=3)
    assert replay.claimed_by is None
    assert exhausted.state is TorrentJobState.FAILED
    assert exhausted.finished_at == NOW
    assert exhausted.last_error_code == "execution_timeout"


@pytest.mark.asyncio
async def test_cancellation_is_immediate_when_queued_and_cooperative_when_running(
    db_session: AsyncSession,
) -> None:
    torrent = await create_torrent(db_session)
    queued = await create_job(db_session, torrent, key="cancel-queued")
    running = await create_job(db_session, torrent, key="cancel-running")

    await request_torrent_job_cancellation(db_session, queued.id, now=NOW)
    assert queued.state is TorrentJobState.CANCELLED
    assert queued.finished_at == NOW

    claimed = await claim_next_torrent_job(
        db_session,
        worker_id="worker-1",
        now=NOW,
        claim_ttl=CLAIM_TTL,
        execution_timeout=EXECUTION_TIMEOUT,
    )
    assert claimed is running
    await request_torrent_job_cancellation(db_session, running.id, now=NOW)
    assert running.state is TorrentJobState.RUNNING
    assert running.cancel_requested_at == NOW

    await cancel_claimed_torrent_job(db_session, running, worker_id="worker-1", now=NOW)
    assert_job_state(running, TorrentJobState.CANCELLED)
    assert running.finished_at == NOW


@pytest.mark.asyncio
async def test_completion_after_cancellation_request_finishes_cancelled(
    db_session: AsyncSession,
) -> None:
    torrent = await create_torrent(db_session)
    job = await create_job(db_session, torrent, key="cancel-after-reconcile")
    await claim_next_torrent_job(
        db_session,
        worker_id="worker-1",
        now=NOW,
        claim_ttl=CLAIM_TTL,
        execution_timeout=EXECUTION_TIMEOUT,
    )
    await request_torrent_job_cancellation(db_session, job.id, now=NOW)

    await complete_torrent_job(db_session, job, worker_id="worker-1", now=NOW)

    assert_job_state(job, TorrentJobState.CANCELLED)


@pytest.mark.asyncio
async def test_postgresql_skip_locked_allows_only_one_claim() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL concurrency test requires WOS_DATABASE_URL")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    torrent_id: uuid.UUID | None = None
    try:
        async with session_factory() as seed_session:
            torrent = ManagedTorrent(
                info_hash=(suffix + "0" * 8)[:40],
                name="Concurrent claim",
                total_size=1,
            )
            seed_session.add(torrent)
            await seed_session.flush()
            torrent_id = torrent.id
            await create_job(seed_session, torrent, key=f"claim-{suffix}")
            await seed_session.commit()

        async with session_factory() as first_session, session_factory() as second_session:
            first_claim = await claim_next_torrent_job(
                first_session,
                worker_id="worker-first",
                now=NOW,
                claim_ttl=CLAIM_TTL,
                execution_timeout=EXECUTION_TIMEOUT,
            )
            second_claim = await claim_next_torrent_job(
                second_session,
                worker_id="worker-second",
                now=NOW,
                claim_ttl=CLAIM_TTL,
                execution_timeout=EXECUTION_TIMEOUT,
            )
            assert first_claim is not None
            assert second_claim is None
            await first_session.commit()
            await second_session.commit()
    finally:
        if torrent_id is not None:
            async with session_factory() as cleanup_session:
                await cleanup_session.execute(
                    delete(ManagedTorrent).where(ManagedTorrent.id == torrent_id)
                )
                await cleanup_session.commit()
        await engine.dispose()
