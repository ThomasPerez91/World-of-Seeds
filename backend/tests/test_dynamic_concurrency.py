from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, ManagedTorrent, ManagedTorrentState, SchedulerState
from app.options import PostgresOptionsRegistry
from app.scheduler.dynamic_concurrency import (
    DYNAMIC_ABOVE_TARGET,
    DYNAMIC_BELOW_TARGET_SCALED_UP,
    DYNAMIC_COMPLETION_COOLDOWN,
    DYNAMIC_DISABLED,
    DYNAMIC_HARD_CAP_REACHED,
    DYNAMIC_IDLE_RESET,
    DYNAMIC_NO_WAITING_CANDIDATE,
    DYNAMIC_OBSERVATION_STARTED,
    DYNAMIC_TARGET_REACHED,
    DynamicConcurrencyConfig,
    DynamicConcurrencyDecision,
    apply_dynamic_concurrency,
    record_dynamic_completion,
)
from app.scheduler.weighted_fair import SchedulerCandidate

NOW = datetime(2026, 9, 21, 10, tzinfo=UTC)
MIB = 1024 * 1024


def _config(**changes: object) -> DynamicConcurrencyConfig:
    values: dict[str, object] = {
        "enabled": True,
        "initial_active": 2,
        "minimum_bytes_per_second": 225 * MIB,
        "target_bytes_per_second": 250 * MIB,
        "evaluation_seconds": 60,
        "step": 1,
        "maximum_active": 8,
        "static_active": 5,
    }
    values.update(changes)
    return DynamicConcurrencyConfig(**values)  # type: ignore[arg-type]


def _state(*, current: int = 2, sampled_at: datetime | None = NOW) -> SchedulerState:
    return SchedulerState(
        id=1,
        dynamic_current_active=current,
        dynamic_sampled_at=sampled_at,
        dynamic_sample_baseline={},
        dynamic_observed_bytes_per_second=0,
        dynamic_active_count=0,
        dynamic_waiting_count=0,
        dynamic_last_decision=DYNAMIC_IDLE_RESET,
    )


def _torrent(index: int, *, downloaded: int, forced: bool = False) -> ManagedTorrent:
    return ManagedTorrent(
        id=uuid.UUID(int=index + 1),
        storage_key=uuid.UUID(int=index + 101),
        info_hash=f"{index + 1:040x}",
        name=f"torrent-{index}",
        total_size=2**40,
        state=ManagedTorrentState.DOWNLOADING,
        progress=0.1,
        desired_active=not forced,
        admin_forced_active=forced,
        desired_priority=None if forced else index,
        last_downloaded_bytes=downloaded,
    )


def _candidate(torrent: ManagedTorrent, *, queued: datetime = NOW) -> SchedulerCandidate:
    return SchedulerCandidate(
        torrent_id=torrent.id,
        user_id=uuid.uuid4(),
        remaining_bytes=1024,
        queued_at=queued,
    )


def _evaluated(
    *,
    rate: int,
    current: int = 2,
    waiting: bool = True,
    forced: bool = False,
    maximum: int = 8,
) -> tuple[SchedulerState, DynamicConcurrencyDecision]:
    active = [_torrent(1, downloaded=rate * 60, forced=forced)]
    state = _state(current=current)
    state.dynamic_sample_baseline = {active[0].info_hash: 0}
    candidates = [_candidate(active[0])]
    if waiting:
        queued = _torrent(2, downloaded=0)
        queued.state = ManagedTorrentState.PAUSED
        queued.desired_active = False
        queued.desired_priority = None
        active.append(queued)
        candidates.append(_candidate(queued))
    decision = apply_dynamic_concurrency(
        state,
        active,
        candidates,
        config=_config(maximum_active=maximum),
        now=NOW + timedelta(seconds=60),
    )
    return state, decision


def test_dynamic_disabled_preserves_static_limit() -> None:
    state, decision = _evaluated(rate=0)
    decision = apply_dynamic_concurrency(
        state,
        (),
        (),
        config=_config(enabled=False, static_active=5),
        now=NOW,
    )
    assert decision.active_limit == 5
    assert state.dynamic_last_decision == DYNAMIC_DISABLED


def test_idle_resets_to_two_without_creating_measurement() -> None:
    state = _state(current=7)
    decision = apply_dynamic_concurrency(state, (), (), config=_config(), now=NOW)
    assert decision.active_limit == 2
    assert state.dynamic_last_decision == DYNAMIC_IDLE_RESET
    assert state.dynamic_sampled_at is None


def test_low_rate_scales_from_two_to_three_only_once_per_period() -> None:
    state, decision = _evaluated(rate=100 * MIB)
    assert decision.active_limit == 3
    assert state.dynamic_last_decision == DYNAMIC_BELOW_TARGET_SCALED_UP

    active = [_torrent(1, downloaded=100 * MIB * 60)]
    waiting = _torrent(2, downloaded=0)
    waiting.state = ManagedTorrentState.PAUSED
    waiting.desired_active = False
    waiting.desired_priority = None
    repeated = apply_dynamic_concurrency(
        state,
        (active[0], waiting),
        (_candidate(active[0]), _candidate(waiting)),
        config=_config(),
        now=NOW + timedelta(seconds=60),
    )
    assert repeated.active_limit == 3


def test_low_rate_scales_again_after_another_complete_period() -> None:
    state, _ = _evaluated(rate=100 * MIB)
    active = _torrent(1, downloaded=200 * MIB * 60)
    waiting = _torrent(2, downloaded=0)
    waiting.state = ManagedTorrentState.PAUSED
    waiting.desired_active = False
    waiting.desired_priority = None
    decision = apply_dynamic_concurrency(
        state,
        (active, waiting),
        (_candidate(active), _candidate(waiting)),
        config=_config(),
        now=NOW + timedelta(seconds=120),
    )
    assert decision.active_limit == 4


@pytest.mark.parametrize(
    ("rate", "reason"),
    ((230 * MIB, DYNAMIC_TARGET_REACHED), (260 * MIB, DYNAMIC_ABOVE_TARGET)),
)
def test_target_or_above_rate_does_not_scale(rate: int, reason: str) -> None:
    state, decision = _evaluated(rate=rate)
    assert decision.active_limit == 2
    assert state.dynamic_last_decision == reason


def test_no_waiting_candidate_does_not_scale() -> None:
    state, decision = _evaluated(rate=0, waiting=False)
    assert decision.active_limit == 2
    assert state.dynamic_last_decision == DYNAMIC_NO_WAITING_CANDIDATE


def test_hard_cap_prevents_unbounded_scaling() -> None:
    state, decision = _evaluated(rate=0, current=8, maximum=8)
    assert decision.active_limit == 8
    assert state.dynamic_last_decision == DYNAMIC_HARD_CAP_REACHED


def test_active_set_change_requires_a_new_complete_window() -> None:
    first = _torrent(1, downloaded=100)
    second = _torrent(2, downloaded=100)
    state = _state()
    state.dynamic_sample_baseline = {first.info_hash: 0}
    decision = apply_dynamic_concurrency(
        state,
        (first, second),
        (_candidate(first), _candidate(second)),
        config=_config(),
        now=NOW + timedelta(seconds=60),
    )
    assert decision.active_limit == 2
    assert state.dynamic_last_decision == DYNAMIC_OBSERVATION_STARTED


def test_administrator_forced_download_is_included_in_measured_rate() -> None:
    state, decision = _evaluated(rate=230 * MIB, forced=True)
    assert decision.active_limit == 2
    assert state.dynamic_active_count == 1
    assert state.dynamic_observed_bytes_per_second == 230 * MIB


@pytest.mark.asyncio
async def test_completion_shrinks_four_to_three_and_persists_cooldown(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dynamic.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session, session.begin():
        await PostgresOptionsRegistry().initialize(session, now=NOW)
        state = _state(current=4)
        session.add(state)
        for index in range(3):
            session.add(_torrent(index, downloaded=10_000 + index))
    async with sessions() as session, session.begin():
        stored_state = await session.get(SchedulerState, 1, with_for_update=True)
        assert stored_state is not None
        await record_dynamic_completion(session, stored_state, now=NOW)
    async with sessions() as session:
        stored = await session.get(SchedulerState, 1)
        assert stored is not None
        assert stored.dynamic_current_active == 3
        assert stored.dynamic_cooldown_until == NOW.replace(tzinfo=None) + timedelta(seconds=60)
        assert stored.dynamic_last_decision == DYNAMIC_COMPLETION_COOLDOWN
        assert len(stored.dynamic_sample_baseline) == 3
    await engine.dispose()


def test_completion_cooldown_blocks_replacement_then_low_rate_scales() -> None:
    active = _torrent(1, downloaded=0)
    waiting = _torrent(2, downloaded=0)
    waiting.state = ManagedTorrentState.PAUSED
    waiting.desired_active = False
    waiting.desired_priority = None
    state = _state(current=3)
    state.dynamic_sample_baseline = {active.info_hash: 0}
    state.dynamic_cooldown_until = NOW + timedelta(seconds=60)
    during = apply_dynamic_concurrency(
        state,
        (active, waiting),
        (_candidate(active), _candidate(waiting)),
        config=_config(),
        now=NOW + timedelta(seconds=30),
    )
    assert during.active_limit == 3
    assert state.dynamic_last_decision == DYNAMIC_COMPLETION_COOLDOWN

    after = apply_dynamic_concurrency(
        state,
        (active, waiting),
        (_candidate(active), _candidate(waiting)),
        config=_config(),
        now=NOW + timedelta(seconds=60),
    )
    assert after.active_limit == 4


def test_completion_cooldown_keeps_limit_when_target_is_reached() -> None:
    active = _torrent(1, downloaded=230 * MIB * 60)
    waiting = _torrent(2, downloaded=0)
    waiting.state = ManagedTorrentState.PAUSED
    waiting.desired_active = False
    waiting.desired_priority = None
    state = _state(current=3)
    state.dynamic_sample_baseline = {active.info_hash: 0}
    state.dynamic_cooldown_until = NOW + timedelta(seconds=60)
    decision = apply_dynamic_concurrency(
        state,
        (active, waiting),
        (_candidate(active), _candidate(waiting)),
        config=_config(),
        now=NOW + timedelta(seconds=60),
    )
    assert decision.active_limit == 3
    assert state.dynamic_last_decision == DYNAMIC_TARGET_REACHED


def test_intermediate_cooldown_cycle_does_not_move_download_baseline() -> None:
    active = _torrent(1, downloaded=115 * MIB * 60)
    waiting = _torrent(2, downloaded=0)
    waiting.state = ManagedTorrentState.PAUSED
    waiting.desired_active = False
    waiting.desired_priority = None
    state = _state(current=3)
    state.dynamic_sample_baseline = {active.info_hash: 0}
    state.dynamic_cooldown_until = NOW + timedelta(seconds=60)
    apply_dynamic_concurrency(
        state,
        (active, waiting),
        (_candidate(active), _candidate(waiting)),
        config=_config(),
        now=NOW + timedelta(seconds=30),
    )
    assert state.dynamic_sample_baseline == {active.info_hash: 0}

    active.last_downloaded_bytes = 230 * MIB * 60
    decision = apply_dynamic_concurrency(
        state,
        (active, waiting),
        (_candidate(active), _candidate(waiting)),
        config=_config(),
        now=NOW + timedelta(seconds=60),
    )
    assert decision.active_limit == 3
    assert state.dynamic_last_decision == DYNAMIC_TARGET_REACHED
