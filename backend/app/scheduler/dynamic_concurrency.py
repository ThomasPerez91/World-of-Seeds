from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DatabaseOption, ManagedTorrent, ManagedTorrentState, SchedulerState
from app.options import OptionValue
from app.scheduler.weighted_fair import SchedulerCandidate

DYNAMIC_DISABLED = "dynamic_disabled"
DYNAMIC_BELOW_TARGET_SCALED_UP = "dynamic_below_target_scaled_up"
DYNAMIC_TARGET_REACHED = "dynamic_target_reached"
DYNAMIC_ABOVE_TARGET = "dynamic_above_target"
DYNAMIC_NO_WAITING_CANDIDATE = "dynamic_no_waiting_candidate"
DYNAMIC_COMPLETION_COOLDOWN = "dynamic_completion_cooldown"
DYNAMIC_IDLE_RESET = "dynamic_idle_reset"
DYNAMIC_HARD_CAP_REACHED = "dynamic_hard_cap_reached"
DYNAMIC_OBSERVATION_STARTED = "dynamic_observation_started"


@dataclass(frozen=True, slots=True)
class DynamicConcurrencyConfig:
    enabled: bool
    initial_active: int
    minimum_bytes_per_second: int
    target_bytes_per_second: int
    evaluation_seconds: int
    step: int
    maximum_active: int
    static_active: int

    @classmethod
    def from_options(cls, options: Mapping[str, OptionValue]) -> DynamicConcurrencyConfig:
        def integer(key: str) -> int:
            value = options.get(key)
            if type(value) is not int:
                raise ValueError(f"{key} must be an integer option")
            return value

        enabled = options.get("WOS_SCHEDULER_DYNAMIC_CONCURRENCY_ENABLED")
        if type(enabled) is not bool:
            raise ValueError("WOS_SCHEDULER_DYNAMIC_CONCURRENCY_ENABLED must be boolean")
        config = cls(
            enabled=enabled,
            initial_active=integer("WOS_SCHEDULER_DYNAMIC_INITIAL_ACTIVE"),
            minimum_bytes_per_second=integer("WOS_SCHEDULER_DYNAMIC_MIN_BYTES_PER_SECOND"),
            target_bytes_per_second=integer("WOS_SCHEDULER_DYNAMIC_TARGET_BYTES_PER_SECOND"),
            evaluation_seconds=integer("WOS_SCHEDULER_DYNAMIC_EVALUATION_SECONDS"),
            step=integer("WOS_SCHEDULER_DYNAMIC_STEP"),
            maximum_active=integer("WOS_SCHEDULER_DYNAMIC_MAX_ACTIVE"),
            static_active=integer("WOS_SCHEDULER_MAX_ACTIVE_GLOBAL"),
        )
        if config.initial_active > config.maximum_active:
            raise ValueError("dynamic initial limit exceeds hard cap")
        if config.minimum_bytes_per_second >= config.target_bytes_per_second:
            raise ValueError("dynamic bandwidth thresholds are not ordered")
        return config


@dataclass(frozen=True, slots=True)
class DynamicConcurrencyDecision:
    active_limit: int
    active_torrent_ids: frozenset[UUID]
    scaled_up: bool = False


def apply_dynamic_concurrency(
    state: SchedulerState,
    torrents: Sequence[ManagedTorrent],
    candidates: Sequence[SchedulerCandidate],
    *,
    config: DynamicConcurrencyConfig,
    now: datetime,
) -> DynamicConcurrencyDecision:
    """Update the bounded durable measurement window and return this cycle's capacity."""

    timestamp = _utc(now)
    active = tuple(
        torrent
        for torrent in torrents
        if torrent.state is ManagedTorrentState.DOWNLOADING
        and (torrent.desired_active or torrent.admin_forced_active)
    )
    active_ids = frozenset(torrent.id for torrent in active)
    waiting_count = sum(1 for candidate in candidates if candidate.torrent_id not in active_ids)
    state.dynamic_active_count = len(active)
    state.dynamic_waiting_count = waiting_count

    if not config.enabled:
        _clear_measurement(state)
        state.dynamic_current_active = config.static_active
        state.dynamic_last_decision = DYNAMIC_DISABLED
        return DynamicConcurrencyDecision(config.static_active, active_ids)

    baseline = _download_baseline(active)
    if not active:
        state.dynamic_current_active = config.initial_active
        state.dynamic_observed_bytes_per_second = 0
        state.dynamic_last_decision = DYNAMIC_IDLE_RESET
        state.dynamic_cooldown_until = None
        _clear_measurement(state)
        return DynamicConcurrencyDecision(config.initial_active, active_ids)

    # Enabling the feature on a busy installation must never pause existing downloads.
    state.dynamic_current_active = max(state.dynamic_current_active, len(active))
    cooldown_until = state.dynamic_cooldown_until
    if cooldown_until is not None and _utc(cooldown_until) > timestamp:
        previous_baseline = _validated_baseline(state.dynamic_sample_baseline)
        if state.dynamic_sampled_at is None or set(previous_baseline) != set(baseline):
            cooldown_until = now + timedelta(seconds=config.evaluation_seconds)
            state.dynamic_cooldown_until = cooldown_until
            _start_measurement(state, baseline, now, config.evaluation_seconds)
        else:
            state.dynamic_next_evaluation_at = cooldown_until
        state.dynamic_last_decision = DYNAMIC_COMPLETION_COOLDOWN
        return DynamicConcurrencyDecision(state.dynamic_current_active, active_ids)
    if cooldown_until is not None:
        state.dynamic_cooldown_until = None

    sampled_at = state.dynamic_sampled_at
    previous_baseline = _validated_baseline(state.dynamic_sample_baseline)
    if sampled_at is None or set(previous_baseline) != set(baseline):
        _start_measurement(state, baseline, now, config.evaluation_seconds)
        state.dynamic_last_decision = DYNAMIC_OBSERVATION_STARTED
        return DynamicConcurrencyDecision(state.dynamic_current_active, active_ids)

    elapsed = (timestamp - _utc(sampled_at)).total_seconds()
    if elapsed < config.evaluation_seconds:
        state.dynamic_next_evaluation_at = sampled_at + timedelta(seconds=config.evaluation_seconds)
        return DynamicConcurrencyDecision(state.dynamic_current_active, active_ids)

    downloaded = sum(
        max(0, current - previous_baseline[info_hash]) for info_hash, current in baseline.items()
    )
    observed = max(0, round(downloaded / elapsed))
    state.dynamic_observed_bytes_per_second = observed
    state.dynamic_last_evaluated_at = now
    scaled_up = False
    if waiting_count == 0:
        reason = DYNAMIC_NO_WAITING_CANDIDATE
    elif state.dynamic_current_active >= config.maximum_active:
        reason = DYNAMIC_HARD_CAP_REACHED
    elif observed < config.minimum_bytes_per_second:
        state.dynamic_current_active = min(
            config.maximum_active,
            state.dynamic_current_active + config.step,
        )
        reason = DYNAMIC_BELOW_TARGET_SCALED_UP
        scaled_up = True
    elif observed <= config.target_bytes_per_second:
        reason = DYNAMIC_TARGET_REACHED
    else:
        reason = DYNAMIC_ABOVE_TARGET
    state.dynamic_last_decision = reason
    _start_measurement(state, baseline, now, config.evaluation_seconds)
    return DynamicConcurrencyDecision(state.dynamic_current_active, active_ids, scaled_up)


def align_measurement_after_selection(
    state: SchedulerState,
    torrents: Sequence[ManagedTorrent],
    selected_ids: set[UUID],
    *,
    config: DynamicConcurrencyConfig,
    now: datetime,
) -> None:
    """Start a fresh full window when this cycle changed the admitted set."""

    if not config.enabled or state.dynamic_cooldown_until is not None:
        return
    measured = tuple(
        torrent for torrent in torrents if torrent.id in selected_ids or torrent.admin_forced_active
    )
    baseline = _download_baseline(measured)
    if set(baseline) != set(_validated_baseline(state.dynamic_sample_baseline)):
        _start_measurement(state, baseline, now, config.evaluation_seconds)


async def record_dynamic_completion(
    session: AsyncSession,
    state: SchedulerState,
    *,
    now: datetime,
) -> None:
    """Shrink capacity immediately after READY and freeze replacement for one full window."""

    rows = tuple(
        (
            await session.scalars(
                select(DatabaseOption).where(
                    DatabaseOption.key.in_(
                        (
                            "WOS_SCHEDULER_DYNAMIC_CONCURRENCY_ENABLED",
                            "WOS_SCHEDULER_DYNAMIC_INITIAL_ACTIVE",
                            "WOS_SCHEDULER_DYNAMIC_EVALUATION_SECONDS",
                        )
                    )
                )
            )
        ).all()
    )
    values = {row.key: row.value for row in rows}
    if values.get("WOS_SCHEDULER_DYNAMIC_CONCURRENCY_ENABLED") is not True:
        return
    initial = values.get("WOS_SCHEDULER_DYNAMIC_INITIAL_ACTIVE")
    evaluation = values.get("WOS_SCHEDULER_DYNAMIC_EVALUATION_SECONDS")
    if type(initial) is not int or type(evaluation) is not int:
        return
    remaining = tuple(
        (
            await session.scalars(
                select(ManagedTorrent)
                .where(
                    ManagedTorrent.state == ManagedTorrentState.DOWNLOADING,
                    or_(
                        ManagedTorrent.desired_active.is_(True),
                        ManagedTorrent.admin_forced_active.is_(True),
                    ),
                )
                .with_for_update()
            )
        ).all()
    )
    state.dynamic_active_count = len(remaining)
    if not remaining:
        state.dynamic_current_active = initial
        state.dynamic_cooldown_until = None
        state.dynamic_last_decision = DYNAMIC_IDLE_RESET
        _clear_measurement(state)
        return
    state.dynamic_current_active = len(remaining)
    state.dynamic_cooldown_until = now + timedelta(seconds=evaluation)
    state.dynamic_last_decision = DYNAMIC_COMPLETION_COOLDOWN
    state.dynamic_observed_bytes_per_second = 0
    _start_measurement(state, _download_baseline(remaining), now, evaluation)
    state.dynamic_next_evaluation_at = state.dynamic_cooldown_until


def _download_baseline(torrents: Sequence[ManagedTorrent]) -> dict[str, int]:
    return {torrent.info_hash: max(0, torrent.last_downloaded_bytes or 0) for torrent in torrents}


def _validated_baseline(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and type(item) is int and item >= 0
    }


def _start_measurement(
    state: SchedulerState,
    baseline: dict[str, int],
    now: datetime,
    evaluation_seconds: int,
) -> None:
    state.dynamic_sample_baseline = baseline
    state.dynamic_sampled_at = now
    state.dynamic_next_evaluation_at = now + timedelta(seconds=evaluation_seconds)


def _clear_measurement(state: SchedulerState) -> None:
    state.dynamic_sample_baseline = {}
    state.dynamic_sampled_at = None
    state.dynamic_next_evaluation_at = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
