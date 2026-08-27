from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.integrations.qbittorrent_v2 import (
    QBittorrentV2ControlResult,
    QBittorrentV2DesiredControl,
)
from app.models import (
    Base,
    DatabaseOption,
    ManagedTorrent,
    ManagedTorrentState,
    SchedulerDeficit,
    SchedulerState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.options import PostgresOptionsRegistry
from app.scheduler.persistence import acquire_scheduler_lease
from app.scheduler.runtime import SchedulerRuntime, _remaining_bytes

NOW = datetime(2026, 8, 21, 22, tzinfo=UTC)


class FakeGateway:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[QBittorrentV2DesiredControl, ...]] = []

    async def apply_managed_controls(
        self, controls: Sequence[QBittorrentV2DesiredControl]
    ) -> QBittorrentV2ControlResult:
        self.calls.append(tuple(controls))
        if self.fail:
            raise RuntimeError("qb_unavailable")
        running = tuple(control.info_hash for control in controls if control.run_state == "running")
        return QBittorrentV2ControlResult(running, (), (), running)


async def _database(
    tmp_path: Path,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scheduler.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        await PostgresOptionsRegistry().initialize(session, now=NOW)
    return engine, sessions


async def _torrent(
    sessions: async_sessionmaker[AsyncSession],
    *,
    username: str,
    info_hash: str,
    size: int,
    created_at: datetime = NOW,
    progress: float = 0,
    state: ManagedTorrentState = ManagedTorrentState.PAUSED,
) -> ManagedTorrent:
    async with sessions() as session, session.begin():
        user = User(username=username, password_hash="test-password-hash")
        torrent = ManagedTorrent(
            info_hash=info_hash,
            name=username,
            total_size=size,
            progress=progress,
            state=state,
            created_at=created_at,
            updated_at=created_at,
        )
        session.add_all([user, torrent])
        await session.flush()
        session.add(
            TorrentRequest(
                user_id=user.id,
                managed_torrent_id=torrent.id,
                state=TorrentRequestState.ACTIVE,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return torrent


@pytest.mark.asyncio
async def test_cycle_persists_desired_controls_ledger_and_applied_generation(
    tmp_path: Path,
) -> None:
    engine, sessions = await _database(tmp_path)
    first = await _torrent(sessions, username="first", info_hash="a" * 40, size=10)
    second = await _torrent(sessions, username="second", info_hash="b" * 40, size=20)
    gateway = FakeGateway()
    runtime = SchedulerRuntime(sessions, gateway, scheduler_id="scheduler-a", clock=lambda: NOW)

    result = await runtime.run_once()

    assert result.leader is True
    assert result.generation == 1
    assert set(result.selected_torrent_ids) == {first.id, second.id}
    assert len(gateway.calls) == 1
    async with sessions() as session:
        state = await session.get(SchedulerState, 1)
        stored = list((await session.scalars(select(ManagedTorrent))).all())
    assert state is not None
    assert state.desired_generation == state.applied_generation == 1
    assert state.rounds > 0
    assert sorted((torrent.desired_active, torrent.desired_priority) for torrent in stored) == [
        (True, 0),
        (True, 1),
    ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_valid_lease_prevents_a_second_scheduler_from_mutating_qb(tmp_path: Path) -> None:
    engine, sessions = await _database(tmp_path)
    await _torrent(sessions, username="owner", info_hash="c" * 40, size=10)
    first_gateway = FakeGateway()
    second_gateway = FakeGateway()
    first = SchedulerRuntime(sessions, first_gateway, scheduler_id="scheduler-a", clock=lambda: NOW)
    second = SchedulerRuntime(
        sessions, second_gateway, scheduler_id="scheduler-b", clock=lambda: NOW
    )

    assert (await first.run_once()).leader is True
    result = await second.run_once()

    assert result.leader is False
    assert second_gateway.calls == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_previously_admitted_torrent_without_active_request_is_stopped(
    tmp_path: Path,
) -> None:
    engine, sessions = await _database(tmp_path)
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(
            info_hash="e" * 40,
            name="orphaned-reference",
            total_size=10,
            state=ManagedTorrentState.DOWNLOADING,
            desired_active=True,
            desired_priority=0,
        )
        session.add(torrent)
    gateway = FakeGateway()

    result = await SchedulerRuntime(
        sessions, gateway, scheduler_id="scheduler-stop", clock=lambda: NOW
    ).run_once()

    assert result.selected_torrent_ids == ()
    assert len(gateway.calls) == 1
    assert gateway.calls[0][0].run_state == "stopped"
    async with sessions() as session:
        stored = await session.get(ManagedTorrent, torrent.id)
    assert stored is not None
    assert stored.desired_active is False
    assert stored.desired_priority is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_ready_seeding_torrent_is_outside_download_slot_control(tmp_path: Path) -> None:
    engine, sessions = await _database(tmp_path)
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(
            info_hash="f" * 40,
            name="seeding",
            total_size=10,
            progress=1,
            state=ManagedTorrentState.READY,
        )
        session.add(torrent)
    gateway = FakeGateway()

    result = await SchedulerRuntime(
        sessions, gateway, scheduler_id="scheduler-seeding", clock=lambda: NOW
    ).run_once()

    assert result.selected_torrent_ids == ()
    assert gateway.calls == [()]
    async with sessions() as session:
        stored = await session.get(ManagedTorrent, torrent.id)
    assert stored is not None
    assert stored.state is ManagedTorrentState.READY
    await engine.dispose()


@pytest.mark.asyncio
async def test_global_active_limit_is_reloaded_between_cycles_with_ten_queued(
    tmp_path: Path,
) -> None:
    engine, sessions = await _database(tmp_path)
    for index in range(10):
        await _torrent(
            sessions,
            username=f"queued-{index}",
            info_hash=f"{index:040x}",
            size=10 + index,
        )
    async with sessions() as session, session.begin():
        option = await session.get(DatabaseOption, "WOS_SCHEDULER_MAX_ACTIVE_GLOBAL")
        assert option is not None
        option.integer_value = 1

    runtime = SchedulerRuntime(
        sessions, FakeGateway(), scheduler_id="scheduler-dynamic-limit", clock=lambda: NOW
    )
    first = await runtime.run_once()

    async with sessions() as session, session.begin():
        option = await session.get(DatabaseOption, "WOS_SCHEDULER_MAX_ACTIVE_GLOBAL")
        assert option is not None
        option.integer_value = 2
    second = await runtime.run_once()

    assert len(first.selected_torrent_ids) == 1
    assert len(second.selected_torrent_ids) == 2
    await engine.dispose()


@pytest.mark.parametrize(
    ("total_size", "progress", "expected"),
    [
        (0, 0.5, None),
        (100, None, 100),
        (100, float("nan"), 100),
        (100, -0.1, 100),
        (100, 0, 100),
        (100, 0.5, 50),
        (100, 0.99, 1),
        (1, 0.99, 1),
        (100, 1, 0),
        (100, 1.1, 0),
    ],
)
def test_remaining_bytes_is_bounded_for_unknown_and_edge_progress(
    total_size: int,
    progress: float | None,
    expected: int | None,
) -> None:
    assert _remaining_bytes(total_size, progress) == expected


@pytest.mark.asyncio
async def test_unapplied_generation_is_reconciled_by_next_owner_after_lease_expiry(
    tmp_path: Path,
) -> None:
    engine, sessions = await _database(tmp_path)
    await _torrent(sessions, username="retry", info_hash="d" * 40, size=10)
    failing = SchedulerRuntime(
        sessions,
        FakeGateway(fail=True),
        scheduler_id="scheduler-dead",
        clock=lambda: NOW,
        lease_ttl=timedelta(seconds=10),
    )

    with pytest.raises(RuntimeError, match="qb_unavailable"):
        await failing.run_once()

    async with sessions() as session:
        failed_state = await session.get(SchedulerState, 1)
    assert failed_state is not None
    assert failed_state.desired_generation == 1
    assert failed_state.applied_generation == 0

    recovered_gateway = FakeGateway()
    recovered = SchedulerRuntime(
        sessions,
        recovered_gateway,
        scheduler_id="scheduler-replacement",
        clock=lambda: NOW + timedelta(seconds=11),
        lease_ttl=timedelta(seconds=10),
    )
    result = await recovered.run_once()

    assert result.leader is True
    assert len(recovered_gateway.calls) == 1
    async with sessions() as session:
        state = await session.get(SchedulerState, 1)
        deficits = list((await session.scalars(select(SchedulerDeficit))).all())
    assert state is not None
    assert state.desired_generation == state.applied_generation == 2
    assert state.rounds > failed_state.rounds
    assert all(deficit.credit >= 0 for deficit in deficits)
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgresql_row_lock_allows_only_one_live_scheduler_owner() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL scheduler lease test requires WOS_DATABASE_URL")

    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_acquired = asyncio.Event()
    release_first = asyncio.Event()
    try:
        async with sessions() as session, session.begin():
            await session.execute(delete(SchedulerDeficit))
            await session.execute(delete(SchedulerState))

        async def acquire(owner: str, *, hold: bool) -> bool:
            async with sessions() as session, session.begin():
                state = await acquire_scheduler_lease(
                    session,
                    owner=owner,
                    now=NOW,
                    ttl=timedelta(seconds=30),
                )
                if hold:
                    first_acquired.set()
                    await release_first.wait()
                return state is not None

        first_task = asyncio.create_task(acquire("scheduler-first", hold=True))
        await first_acquired.wait()
        second_task = asyncio.create_task(acquire("scheduler-second", hold=False))
        await asyncio.sleep(0.05)
        assert second_task.done() is False
        release_first.set()

        assert await first_task is True
        assert await second_task is False
    finally:
        async with sessions() as session, session.begin():
            await session.execute(delete(SchedulerDeficit))
            await session.execute(delete(SchedulerState))
        await engine.dispose()
