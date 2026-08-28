from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from app.options import OptionValue

_SMALL_COST = 1
_MEDIUM_COST = 2
_LARGE_COST = 4
_MAX_USER_WEIGHT = 100


class TorrentSizeClass(StrEnum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("scheduler timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _integer_option(options: Mapping[str, OptionValue], key: str) -> int:
    value = options.get(key)
    if type(value) is not int:
        raise ValueError(f"{key} must be an integer option")
    return value


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    max_active_global: int
    max_active_per_user: int
    small_torrent_bytes: int
    medium_torrent_bytes: int
    deficit_quantum: int
    aging_interval_seconds: int
    aging_max_bonus: int

    def __post_init__(self) -> None:
        if not 1 <= self.max_active_global <= 200:
            raise ValueError("global active limit must be between 1 and 200")
        if not 1 <= self.max_active_per_user <= 50:
            raise ValueError("per-user active limit must be between 1 and 50")
        if not 0 < self.small_torrent_bytes < self.medium_torrent_bytes:
            raise ValueError("torrent size thresholds must be positive and ordered")
        if not 1 <= self.deficit_quantum <= 16:
            raise ValueError("deficit quantum must be between 1 and 16")
        if not 60 <= self.aging_interval_seconds <= 86_400:
            raise ValueError("aging interval must be between 60 and 86400 seconds")
        if not 0 <= self.aging_max_bonus < _LARGE_COST:
            raise ValueError("aging bonus must be between 0 and 3")

    @classmethod
    def from_options(cls, options: Mapping[str, OptionValue]) -> SchedulerPolicy:
        return cls(
            max_active_global=_integer_option(options, "WOS_SCHEDULER_MAX_ACTIVE_GLOBAL"),
            max_active_per_user=_integer_option(options, "WOS_SCHEDULER_MAX_ACTIVE_PER_USER"),
            small_torrent_bytes=_integer_option(options, "WOS_SCHEDULER_SMALL_TORRENT_BYTES"),
            medium_torrent_bytes=_integer_option(options, "WOS_SCHEDULER_MEDIUM_TORRENT_BYTES"),
            deficit_quantum=_integer_option(options, "WOS_SCHEDULER_DEFICIT_QUANTUM"),
            aging_interval_seconds=_integer_option(options, "WOS_SCHEDULER_AGING_INTERVAL_SECONDS"),
            aging_max_bonus=_integer_option(options, "WOS_SCHEDULER_AGING_MAX_BONUS"),
        )


@dataclass(frozen=True, slots=True)
class SchedulerCandidate:
    torrent_id: uuid.UUID
    user_id: uuid.UUID
    remaining_bytes: int
    queued_at: datetime
    user_weight: int = 1
    stalled: bool = False
    beneficiary_user_ids: tuple[uuid.UUID, ...] = ()
    beneficiary_weights: Mapping[uuid.UUID, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.remaining_bytes <= 0:
            raise ValueError("remaining bytes must be positive")
        _utc(self.queued_at)
        if not 1 <= self.user_weight <= _MAX_USER_WEIGHT:
            raise ValueError("user weight must be between 1 and 100")
        beneficiaries = self.beneficiaries
        if set(self.beneficiary_weights) - set(beneficiaries):
            raise ValueError("beneficiary weights must belong to this physical torrent")
        if any(not 1 <= weight <= _MAX_USER_WEIGHT for weight in self.beneficiary_weights.values()):
            raise ValueError("beneficiary weights must be between 1 and 100")

    @property
    def beneficiaries(self) -> tuple[uuid.UUID, ...]:
        return tuple(sorted({self.user_id, *self.beneficiary_user_ids}, key=str))

    def weight_for(self, user_id: uuid.UUID) -> int:
        return self.beneficiary_weights.get(user_id, self.user_weight)


@dataclass(frozen=True, slots=True)
class SchedulerLedger:
    deficits: Mapping[uuid.UUID, int] = field(default_factory=dict)
    rounds: int = 0
    cursor_user_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.rounds < 0:
            raise ValueError("scheduler rounds cannot be negative")
        if any(value < 0 for value in self.deficits.values()):
            raise ValueError("scheduler deficits cannot be negative")


@dataclass(frozen=True, slots=True)
class SchedulerDecision:
    candidate: SchedulerCandidate
    beneficiary_user_id: uuid.UUID
    size_class: TorrentSizeClass
    base_cost: int
    charged_cost: int
    wait_seconds: int


@dataclass(frozen=True, slots=True)
class SchedulerResult:
    selected: tuple[SchedulerDecision, ...]
    ledger: SchedulerLedger
    stalled_torrent_ids: tuple[uuid.UUID, ...]
    capacity_remaining: int


def _size_class(policy: SchedulerPolicy, remaining_bytes: int) -> tuple[TorrentSizeClass, int]:
    if remaining_bytes <= policy.small_torrent_bytes:
        return TorrentSizeClass.SMALL, _SMALL_COST
    if remaining_bytes <= policy.medium_torrent_bytes:
        return TorrentSizeClass.MEDIUM, _MEDIUM_COST
    return TorrentSizeClass.LARGE, _LARGE_COST


def _decision(
    policy: SchedulerPolicy,
    candidate: SchedulerCandidate,
    now: datetime,
    *,
    beneficiary_user_id: uuid.UUID,
) -> SchedulerDecision:
    size_class, base_cost = _size_class(policy, candidate.remaining_bytes)
    wait_seconds = max(0, int((_utc(now) - _utc(candidate.queued_at)).total_seconds()))
    aging_bonus = min(
        policy.aging_max_bonus,
        wait_seconds // policy.aging_interval_seconds,
    )
    return SchedulerDecision(
        candidate=candidate,
        beneficiary_user_id=beneficiary_user_id,
        size_class=size_class,
        base_cost=base_cost,
        charged_cost=max(1, base_cost - aging_bonus),
        wait_seconds=wait_seconds,
    )


def _candidate_order(candidate: SchedulerCandidate) -> tuple[datetime, str]:
    return (_utc(candidate.queued_at), str(candidate.torrent_id))


def select_torrents(
    candidates: Sequence[SchedulerCandidate],
    *,
    policy: SchedulerPolicy,
    now: datetime,
    active_global: int,
    active_by_user: Mapping[uuid.UUID, int],
    ledger: SchedulerLedger | None = None,
) -> SchedulerResult:
    """Select physical torrents without external effects and return the next durable ledger.

    Callers provide every active beneficiary for one physical torrent. A shared torrent is present
    in each beneficiary queue, but is selected and charged exactly once. The durable user cursor
    rotates that charge fairly across cycles and process restarts. Stalled torrents never consume
    scarce admission slots; they must be reconsidered after a later health snapshot reports
    sources again.
    """

    _utc(now)
    if active_global < 0:
        raise ValueError("global active count cannot be negative")
    if any(count < 0 for count in active_by_user.values()):
        raise ValueError("per-user active counts cannot be negative")

    torrent_ids = [candidate.torrent_id for candidate in candidates]
    if len(torrent_ids) != len(set(torrent_ids)):
        raise ValueError("each physical torrent must have exactly one scheduler candidate")

    weights: dict[uuid.UUID, int] = {}
    queues: dict[uuid.UUID, list[SchedulerCandidate]] = defaultdict(list)
    stalled_ids: list[uuid.UUID] = []
    active_users: set[uuid.UUID] = set()
    for candidate in candidates:
        if candidate.stalled:
            stalled_ids.append(candidate.torrent_id)
        for user_id in candidate.beneficiaries:
            active_users.add(user_id)
            user_weight = candidate.weight_for(user_id)
            previous_weight = weights.setdefault(user_id, user_weight)
            if previous_weight != user_weight:
                raise ValueError("all candidates for a user must have the same weight")
            if (
                not candidate.stalled
                and active_by_user.get(user_id, 0) < policy.max_active_per_user
            ):
                queues[user_id].append(candidate)

    for queue in queues.values():
        queue.sort(key=_candidate_order)

    slots = max(0, policy.max_active_global - active_global)
    counts = dict(active_by_user)
    previous = ledger or SchedulerLedger()
    deficits = {user_id: value for user_id, value in previous.deficits.items() if value >= 0}
    rounds = previous.rounds
    selected: list[SchedulerDecision] = []
    deficit_cap = max(_LARGE_COST, policy.deficit_quantum * _MAX_USER_WEIGHT) * 8

    cursor_user_id = previous.cursor_user_id
    while slots > 0 and queues:
        eligible_users = sorted(
            [
                user_id
                for user_id, queue in queues.items()
                if queue and counts.get(user_id, 0) < policy.max_active_per_user
            ],
            key=lambda user_id: (_candidate_order(queues[user_id][0]), str(user_id)),
        )
        if not eligible_users:
            break

        if cursor_user_id in eligible_users:
            cursor_index = eligible_users.index(cursor_user_id)
            chosen_user = eligible_users[(cursor_index + 1) % len(eligible_users)]
        else:
            chosen_user = eligible_users[0]
        cursor_user_id = chosen_user
        rounds += 1
        deficits[chosen_user] = min(
            deficit_cap,
            deficits.get(chosen_user, 0) + policy.deficit_quantum * weights[chosen_user],
        )

        while queues[chosen_user] and slots > 0:
            decision = _decision(
                policy,
                queues[chosen_user][0],
                now,
                beneficiary_user_id=chosen_user,
            )
            if deficits[chosen_user] < decision.charged_cost:
                break
            selected.append(decision)
            deficits[chosen_user] -= decision.charged_cost
            counts[chosen_user] = counts.get(chosen_user, 0) + 1
            selected_torrent_id = decision.candidate.torrent_id
            for queue in queues.values():
                queue[:] = [
                    candidate for candidate in queue if candidate.torrent_id != selected_torrent_id
                ]
            slots -= 1
            if counts[chosen_user] >= policy.max_active_per_user:
                break

    next_deficits = {
        user_id: deficits.get(user_id, 0)
        for user_id in sorted(active_users, key=str)
        if deficits.get(user_id, 0) > 0
    }
    return SchedulerResult(
        selected=tuple(selected),
        ledger=SchedulerLedger(
            deficits=next_deficits,
            rounds=rounds,
            cursor_user_id=cursor_user_id if active_users else None,
        ),
        stalled_torrent_ids=tuple(sorted(stalled_ids, key=str)),
        capacity_remaining=slots,
    )
