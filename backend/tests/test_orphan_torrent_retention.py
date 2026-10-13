from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_v2_scheduler_runtime import NOW, FakeGateway, _database, _torrent

from app.models import (
    DatabaseOption,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    UserStorageUsage,
)
from app.scheduler.runtime import SchedulerRuntime
from app.torrents import cancel_owned_torrent_request, create_or_get_torrent_request


async def cancel_last(sessions: async_sessionmaker[AsyncSession], torrent_id: object) -> None:
    async with sessions() as session, session.begin():
        request = await session.scalar(
            select(TorrentRequest).where(TorrentRequest.managed_torrent_id == torrent_id)
        )
        assert request is not None
        result = await cancel_owned_torrent_request(
            session,
            user_id=request.user_id,
            torrent_request_id=request.id,
            retention_hours=48,
            now=NOW,
        )
        assert result is not None and result.purge_scheduled


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled", [False, True])
@pytest.mark.parametrize("forced", [False, True])
async def test_last_unsubscribe_stops_partial_and_preserves_retention(
    tmp_path: Path,
    stalled: bool,
    forced: bool,
) -> None:
    engine, sessions = await _database(tmp_path)
    torrent = await _torrent(
        sessions,
        username="partial-retained",
        info_hash="1" * 40,
        size=100,
        progress=0.4,
        state=ManagedTorrentState.DOWNLOADING,
    )
    async with sessions() as session, session.begin():
        stored = await session.get(ManagedTorrent, torrent.id)
        assert stored is not None
        stored.desired_active = True
        stored.desired_priority = 0
        stored.admin_forced_active = forced
        stored.qb_state = "stalledDL" if stalled else "downloading"
        stored.stall_count = 1 if stalled else 0
        stored.scheduler_retry_at = NOW + timedelta(minutes=10) if stalled else None
    await cancel_last(sessions, torrent.id)
    gateway = FakeGateway()
    await SchedulerRuntime(
        sessions, gateway, scheduler_id="orphan-stop", clock=lambda: NOW
    ).run_once()
    assert gateway.calls[0][0].run_state == "stopped"
    async with sessions() as session:
        stored = await session.get(ManagedTorrent, torrent.id)
        request = await session.scalar(
            select(TorrentRequest).where(TorrentRequest.managed_torrent_id == torrent.id)
        )
        job = await session.scalar(
            select(TorrentJob).where(TorrentJob.managed_torrent_id == torrent.id)
        )
        assert stored is not None and request is not None and job is not None
        assert stored.progress == 0.4
        assert stored.storage_key == torrent.storage_key
        assert stored.state is ManagedTorrentState.DOWNLOADING
        assert stored.desired_active is False
        assert stored.admin_forced_active is False
        assert stored.purge_after == (NOW + timedelta(hours=48)).replace(tzinfo=None)
        assert request.state is TorrentRequestState.CANCELLED
        assert job.state is TorrentJobState.QUEUED
        assert job.available_at == stored.purge_after
    await engine.dispose()


@pytest.mark.asyncio
async def test_retained_stalled_orphan_releases_capacity_to_another_torrent(tmp_path: Path) -> None:
    engine, sessions = await _database(tmp_path)
    orphan = await _torrent(
        sessions,
        username="orphan",
        info_hash="2" * 40,
        size=100,
        progress=0.4,
        state=ManagedTorrentState.DOWNLOADING,
    )
    healthy = await _torrent(sessions, username="healthy", info_hash="3" * 40, size=10)
    async with sessions() as session, session.begin():
        stored = await session.get(ManagedTorrent, orphan.id)
        assert stored is not None
        stored.desired_active = True
        stored.desired_priority = 0
        stored.state = ManagedTorrentState.PAUSED
        stored.scheduler_retry_at = NOW + timedelta(minutes=10)
        limit = await session.get(DatabaseOption, "WOS_SCHEDULER_MAX_ACTIVE_GLOBAL")
        dynamic = await session.get(DatabaseOption, "WOS_SCHEDULER_DYNAMIC_CONCURRENCY_ENABLED")
        assert limit is not None and dynamic is not None
        limit.integer_value = 1
        dynamic.boolean_value = False
    await cancel_last(sessions, orphan.id)
    gateway = FakeGateway()
    cycle = await SchedulerRuntime(
        sessions, gateway, scheduler_id="capacity-stop", clock=lambda: NOW
    ).run_once()
    assert cycle.selected_torrent_ids == (healthy.id,)
    controls = {c.info_hash: c.run_state for c in gateway.calls[0]}
    assert controls == {orphan.info_hash: "stopped", healthy.info_hash: "running"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_remaining_owner_keeps_download_and_quota(tmp_path: Path) -> None:
    engine, sessions = await _database(tmp_path)
    torrent = await _torrent(
        sessions,
        username="first-owner",
        info_hash="4" * 40,
        size=100,
        progress=0.4,
        state=ManagedTorrentState.DOWNLOADING,
    )
    async with sessions() as session, session.begin():
        from app.models import User

        other = User(username="second-owner", password_hash="hash")
        request = TorrentRequest(
            user=other, managed_torrent_id=torrent.id, state=TorrentRequestState.ACTIVE
        )
        session.add_all([request, UserStorageUsage(user=other, logical_bytes=100)])
        first = await session.scalar(
            select(TorrentRequest).where(
                TorrentRequest.managed_torrent_id == torrent.id, TorrentRequest.user_id != other.id
            )
        )
        assert first is not None
        result = await cancel_owned_torrent_request(
            session, user_id=first.user_id, torrent_request_id=first.id, retention_hours=48, now=NOW
        )
        assert result is not None and not result.purge_scheduled
        other_id = other.id
    gateway = FakeGateway()
    cycle = await SchedulerRuntime(
        sessions, gateway, scheduler_id="shared", clock=lambda: NOW
    ).run_once()
    assert cycle.selected_torrent_ids == (torrent.id,)
    async with sessions() as session:
        stored = await session.get(ManagedTorrent, torrent.id)
        usage = await session.get(UserStorageUsage, other_id)
        assert stored is not None and stored.purge_after is None
        assert usage is not None and usage.logical_bytes == 100
    await engine.dispose()


@pytest.mark.asyncio
async def test_resubscription_reuses_partial_and_cancels_purge(tmp_path: Path) -> None:
    engine, sessions = await _database(tmp_path)
    torrent = await _torrent(
        sessions,
        username="returning-owner",
        info_hash="5" * 40,
        size=100,
        progress=0.4,
        state=ManagedTorrentState.DOWNLOADING,
    )
    await cancel_last(sessions, torrent.id)
    runtime = SchedulerRuntime(sessions, FakeGateway(), scheduler_id="return", clock=lambda: NOW)
    await runtime.run_once()
    async with sessions() as session, session.begin():
        original = await session.scalar(
            select(TorrentRequest).where(TorrentRequest.managed_torrent_id == torrent.id)
        )
        assert original is not None
        result = await create_or_get_torrent_request(
            session,
            user_id=original.user_id,
            info_hash=torrent.info_hash,
            name=torrent.name,
            total_size=100,
            now=NOW + timedelta(minutes=1),
        )
        assert result.managed_torrent.id == torrent.id
        assert result.managed_torrent.storage_key == torrent.storage_key
        assert result.managed_torrent.progress == 0.4
        assert result.managed_torrent.purge_after is None
        jobs = (
            await session.scalars(
                select(TorrentJob).where(TorrentJob.managed_torrent_id == torrent.id)
            )
        ).all()
        assert all(job.state is TorrentJobState.CANCELLED for job in jobs)
    cycle = await runtime.run_once()
    assert cycle.selected_torrent_ids == (torrent.id,)
    await engine.dispose()


@pytest.mark.asyncio
async def test_stop_is_replayed_after_gateway_failure(tmp_path: Path) -> None:
    engine, sessions = await _database(tmp_path)
    torrent = await _torrent(
        sessions,
        username="gateway-outage",
        info_hash="6" * 40,
        size=100,
        progress=0.4,
        state=ManagedTorrentState.DOWNLOADING,
    )
    async with sessions() as session, session.begin():
        stored = await session.get(ManagedTorrent, torrent.id)
        assert stored is not None
        stored.desired_active = True
        stored.desired_priority = 0
    await cancel_last(sessions, torrent.id)
    runtime = SchedulerRuntime(
        sessions, FakeGateway(fail=True), scheduler_id="gateway-replay", clock=lambda: NOW
    )
    with pytest.raises(RuntimeError, match="qb_unavailable"):
        await runtime.run_once()
    recovered = FakeGateway()
    await SchedulerRuntime(
        sessions, recovered, scheduler_id="gateway-replay", clock=lambda: NOW
    ).run_once()
    assert recovered.calls[0][0].run_state == "stopped"
    await engine.dispose()
