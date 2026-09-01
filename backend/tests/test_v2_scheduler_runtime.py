from __future__ import annotations

import asyncio
import math
import os
import uuid
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

from app.coordination import TorrentEventType, TorrentRealtimeEvent
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


class RecordingRedis:
    def __init__(self) -> None:
        self.events: list[tuple[uuid.UUID, TorrentRealtimeEvent]] = []

    async def publish_torrent_event(
        self,
        user_id: uuid.UUID,
        event: TorrentRealtimeEvent,
    ) -> bool:
        self.events.append((user_id, event))
        return True


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


async def _add_owner(
    sessions: async_sessionmaker[AsyncSession],
    torrent: ManagedTorrent,
    *,
    username: str,
) -> uuid.UUID:
    async with sessions() as session, session.begin():
        user = User(username=username, password_hash="test-password-hash")
        session.add(user)
        await session.flush()
        session.add(
            TorrentRequest(
                user_id=user.id,
                managed_torrent_id=torrent.id,
                state=TorrentRequestState.ACTIVE,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        return user.id


async def _backlog(
    sessions: async_sessionmaker[AsyncSession],
    total: int,
) -> set[str]:
    expected: set[str] = set()
    async with sessions() as session, session.begin():
        for index in range(total):
            user_id = uuid.UUID(int=10_000 + index)
            torrent_id = uuid.UUID(int=20_000 + index)
            info_hash = f"{30_000 + index:040x}"
            created_at = NOW + timedelta(microseconds=index)
            user = User(
                id=user_id,
                username=f"window-{total}-{index}",
                password_hash="test-password-hash",
            )
            torrent = ManagedTorrent(
                id=torrent_id,
                storage_key=uuid.UUID(int=40_000 + index),
                info_hash=info_hash,
                name=f"window-{index}",
                total_size=(index + 1) * 1024**3,
                state=ManagedTorrentState.PAUSED,
                scheduler_retry_at=(NOW + timedelta(minutes=3) if index % 29 == 0 else None),
                created_at=created_at,
                updated_at=created_at,
            )
            request = TorrentRequest(
                id=uuid.UUID(int=50_000 + index),
                user_id=user_id,
                managed_torrent_id=torrent_id,
                state=TorrentRequestState.ACTIVE,
                created_at=created_at,
                updated_at=created_at,
            )
            session.add_all((user, torrent, request))
            expected.add(info_hash)
    return expected


@pytest.mark.asyncio
async def test_cycle_persists_desired_controls_ledger_and_applied_generation(
    tmp_path: Path,
) -> None:
    engine, sessions = await _database(tmp_path)
    first = await _torrent(sessions, username="first", info_hash="a" * 40, size=10)
    second = await _torrent(sessions, username="second", info_hash="b" * 40, size=20)
    gateway = FakeGateway()
    redis = RecordingRedis()
    runtime = SchedulerRuntime(
        sessions,
        gateway,
        scheduler_id="scheduler-a",
        redis=redis,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    result = await runtime.run_once()

    assert result.leader is True
    assert result.generation == 1
    assert set(result.selected_torrent_ids) == {first.id, second.id}
    assert len(gateway.calls) == 1
    async with sessions() as session:
        state = await session.get(SchedulerState, 1)
        stored = list((await session.scalars(select(ManagedTorrent))).all())
        requests = list((await session.scalars(select(TorrentRequest))).all())
    assert state is not None
    assert state.desired_generation == state.applied_generation == 1
    assert state.rounds > 0
    assert sorted((torrent.desired_active, torrent.desired_priority) for torrent in stored) == [
        (True, 0),
        (True, 1),
    ]
    assert sorted(
        (user_id, event.event_type, event.request_id) for user_id, event in redis.events
    ) == sorted((request.user_id, TorrentEventType.STARTED, request.id) for request in requests)
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
async def test_purge_pending_download_is_durably_stopped_before_retention(
    tmp_path: Path,
) -> None:
    engine, sessions = await _database(tmp_path)
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(
            info_hash="9" * 40,
            name="cancelled-downloading",
            total_size=10,
            progress=0.4,
            state=ManagedTorrentState.PURGE_PENDING,
            purge_after=NOW + timedelta(hours=48),
            desired_active=False,
            desired_priority=None,
            purge_stop_pending=True,
        )
        session.add(torrent)
    failing = SchedulerRuntime(
        sessions,
        FakeGateway(fail=True),
        scheduler_id="scheduler-purge-stop-failure",
        clock=lambda: NOW,
        lease_ttl=timedelta(seconds=10),
    )

    with pytest.raises(RuntimeError, match="qb_unavailable"):
        await failing.run_once()
    async with sessions() as session:
        pending = await session.get(ManagedTorrent, torrent.id)
        assert pending is not None and pending.purge_stop_pending is True

    gateway = FakeGateway()
    recovered = SchedulerRuntime(
        sessions,
        gateway,
        scheduler_id="scheduler-purge-stop-replay",
        clock=lambda: NOW + timedelta(seconds=11),
        lease_ttl=timedelta(seconds=10),
    )
    result = await recovered.run_once()

    assert result.selected_torrent_ids == ()
    assert len(gateway.calls) == 2
    assert len(gateway.calls[0]) == 1
    assert gateway.calls[0][0].run_state == "stopped"
    assert gateway.calls[0][0].info_hash == "9" * 40
    async with sessions() as session:
        stopped = await session.get(ManagedTorrent, torrent.id)
        assert stopped is not None
        assert stopped.state is ManagedTorrentState.PURGE_PENDING
        assert stopped.purge_stop_pending is False
    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_stop_batch_never_truncates_two_hundred_active_controls(
    tmp_path: Path,
) -> None:
    engine, sessions = await _database(tmp_path)
    async with sessions() as session, session.begin():
        for index in range(200):
            session.add(
                ManagedTorrent(
                    id=uuid.UUID(int=50_000 + index),
                    info_hash=f"{60_000 + index:040x}",
                    name=f"active-{index}",
                    total_size=10,
                    state=ManagedTorrentState.DOWNLOADING,
                    desired_active=True,
                    desired_priority=index,
                )
            )
        pending = ManagedTorrent(
            id=uuid.UUID(int=70_000),
            info_hash=f"{70_000:040x}",
            name="pending-stop",
            total_size=10,
            state=ManagedTorrentState.PURGE_PENDING,
            purge_after=NOW,
            desired_active=False,
            purge_stop_pending=True,
            lifecycle_generation=1,
        )
        session.add(pending)
    gateway = FakeGateway()

    await SchedulerRuntime(
        sessions,
        gateway,
        scheduler_id="scheduler-full-active-plus-stop",
        clock=lambda: NOW,
    ).run_once()

    assert [len(call) for call in gateway.calls] == [1, 200]
    assert gateway.calls[0][0].info_hash == pending.info_hash
    assert gateway.calls[0][0].run_state == "stopped"
    assert {control.info_hash for control in gateway.calls[1]} == {
        f"{60_000 + index:040x}" for index in range(200)
    }
    async with sessions() as session:
        stored = await session.get(ManagedTorrent, pending.id)
        assert stored is not None and stored.purge_stop_pending is False
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


@pytest.mark.asyncio
async def test_selection_change_invalidates_other_queue_owners_without_event_storm(
    tmp_path: Path,
) -> None:
    engine, sessions = await _database(tmp_path)
    for index in range(3):
        await _torrent(
            sessions,
            username=f"queue-event-{index}",
            info_hash=f"{80 + index:040x}",
            size=10 + index,
        )
    async with sessions() as session, session.begin():
        option = await session.get(DatabaseOption, "WOS_SCHEDULER_MAX_ACTIVE_GLOBAL")
        assert option is not None
        option.integer_value = 1
    redis = RecordingRedis()

    await SchedulerRuntime(
        sessions,
        FakeGateway(),
        scheduler_id="scheduler-queue-events",
        redis=redis,  # type: ignore[arg-type]
        clock=lambda: NOW,
    ).run_once()

    assert len(redis.events) == 3
    assert [event.event_type for _, event in redis.events].count(TorrentEventType.STARTED) == 1
    assert [event.event_type for _, event in redis.events].count(
        TorrentEventType.QUEUE_CHANGED
    ) == 2
    assert all(
        set(event.payload()) == {"type", "request_id", "occurred_at"} for _, event in redis.events
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_cooldown_survives_scheduler_restart(tmp_path: Path) -> None:
    engine, sessions = await _database(tmp_path)
    cooling = await _torrent(
        sessions,
        username="cooling",
        info_hash="1" * 40,
        size=10,
    )
    async with sessions() as session, session.begin():
        stored = await session.get(ManagedTorrent, cooling.id)
        assert stored is not None
        stored.scheduler_retry_at = NOW + timedelta(minutes=3)
        stored.stall_count = 1

    before = await SchedulerRuntime(
        sessions,
        FakeGateway(),
        scheduler_id="scheduler-before-restart",
        clock=lambda: NOW,
        lease_ttl=timedelta(seconds=10),
    ).run_once()
    after = await SchedulerRuntime(
        sessions,
        FakeGateway(),
        scheduler_id="scheduler-after-restart",
        clock=lambda: NOW + timedelta(minutes=3, seconds=1),
        lease_ttl=timedelta(seconds=10),
    ).run_once()

    assert before.selected_torrent_ids == ()
    assert after.selected_torrent_ids == (cooling.id,)
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_slots_are_bounded_with_one_hundred_eligible_torrents(tmp_path: Path) -> None:
    engine, sessions = await _database(tmp_path)
    for index in range(100):
        await _torrent(
            sessions,
            username=f"backlog-{index}",
            info_hash=f"{index + 2:040x}",
            size=20 + index,
        )

    result = await SchedulerRuntime(
        sessions,
        FakeGateway(),
        scheduler_id="scheduler-hundred",
        clock=lambda: NOW,
    ).run_once()

    assert len(result.selected_torrent_ids) == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_shared_beneficiary_rotation_is_persisted_across_runtime_restart(
    tmp_path: Path,
) -> None:
    engine, sessions = await _database(tmp_path)
    shared = await _torrent(
        sessions,
        username="shared-first",
        info_hash="2" * 40,
        size=10,
    )
    second_user_id = await _add_owner(sessions, shared, username="shared-second")
    async with sessions() as session:
        requests = list(
            (
                await session.scalars(
                    select(TorrentRequest).where(TorrentRequest.managed_torrent_id == shared.id)
                )
            ).all()
        )
    owner_ids = {request.user_id for request in requests}
    assert second_user_id in owner_ids

    cursors: list[uuid.UUID | None] = []
    for _ in range(3):
        await SchedulerRuntime(
            sessions,
            FakeGateway(),
            scheduler_id="scheduler-shared-restart",
            clock=lambda: NOW,
        ).run_once()
        async with sessions() as session:
            state = await session.get(SchedulerState, 1)
        assert state is not None
        cursors.append(state.cursor_user_id)

    assert cursors[0] in owner_ids
    assert cursors[1] in owner_ids
    assert cursors[0] != cursors[1]
    assert cursors[2] == cursors[0]
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [199, 200, 201, 500, 1_000])
async def test_bounded_control_windows_progress_through_the_complete_backlog(
    tmp_path: Path,
    total: int,
) -> None:
    engine, sessions = await _database(tmp_path)
    expected = await _backlog(sessions, total)
    gateway = FakeGateway()
    runtime = SchedulerRuntime(
        sessions,
        gateway,
        scheduler_id=f"scheduler-window-{total}",
        clock=lambda: NOW,
    )

    for _ in range(math.ceil(total / 198) + 3):
        result = await runtime.run_once()
        assert len(result.selected_torrent_ids) <= 2

    observed = {control.info_hash for controls in gateway.calls for control in controls}
    assert observed == expected
    assert all(len(controls) <= 200 for controls in gateway.calls)
    assert all(
        len({control.info_hash for control in controls}) == len(controls)
        for controls in gateway.calls
    )
    async with sessions() as session:
        state = await session.get(SchedulerState, 1)
    assert state is not None
    assert state.scan_cursor_created_at is not None
    assert state.scan_cursor_id is not None
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
