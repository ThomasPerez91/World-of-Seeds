import random
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.options import OPTION_SPECS
from app.scheduler import (
    SchedulerCandidate,
    SchedulerLedger,
    SchedulerPolicy,
    SchedulerResult,
    TorrentSizeClass,
    select_torrents,
)

NOW = datetime(2026, 8, 21, 20, tzinfo=UTC)
GIB = 1024**3


def policy(**changes: int) -> SchedulerPolicy:
    values = {
        "max_active_global": 8,
        "max_active_per_user": 8,
        "small_torrent_bytes": 10 * GIB,
        "medium_torrent_bytes": 50 * GIB,
        "deficit_quantum": 1,
        "aging_interval_seconds": 3600,
        "aging_max_bonus": 3,
    }
    values.update(changes)
    return SchedulerPolicy(**values)


def candidate(
    user_id: uuid.UUID,
    size: int,
    *,
    torrent_number: int,
    queued_at: datetime = NOW,
    weight: int = 1,
    stalled: bool = False,
    beneficiaries: tuple[uuid.UUID, ...] = (),
    beneficiary_weights: dict[uuid.UUID, int] | None = None,
) -> SchedulerCandidate:
    return SchedulerCandidate(
        torrent_id=uuid.UUID(int=torrent_number),
        user_id=user_id,
        remaining_bytes=size,
        queued_at=queued_at,
        user_weight=weight,
        stalled=stalled,
        beneficiary_user_ids=beneficiaries,
        beneficiary_weights=beneficiary_weights or {},
    )


def selected_ids(result: SchedulerResult) -> list[uuid.UUID]:
    return [decision.candidate.torrent_id for decision in result.selected]


def test_policy_is_built_from_the_typed_v2_option_registry() -> None:
    options = {spec.key: spec.default for spec in OPTION_SPECS}

    configured = SchedulerPolicy.from_options(options)

    assert configured.max_active_global == 2
    assert configured.max_active_per_user == 2
    assert configured.small_torrent_bytes == 10 * GIB
    assert configured.medium_torrent_bytes == 50 * GIB
    assert configured.deficit_quantum == 1
    assert configured.aging_interval_seconds == 3600
    assert configured.aging_max_bonus == 3


def test_small_torrents_finish_first_without_dropping_the_large_torrent() -> None:
    small_user = uuid.UUID(int=1)
    large_user = uuid.UUID(int=2)
    jobs = [candidate(small_user, 2 * GIB, torrent_number=index) for index in range(10, 20)]
    large = candidate(large_user, 80 * GIB, torrent_number=100)

    result = select_torrents(
        [large, *jobs],
        policy=policy(max_active_global=5, max_active_per_user=5),
        now=NOW,
        active_global=0,
        active_by_user={},
    )

    assert [decision.size_class for decision in result.selected[:2]] == [
        TorrentSizeClass.SMALL,
        TorrentSizeClass.SMALL,
    ]
    assert large.torrent_id in selected_ids(result)


def test_accumulated_deficit_prevents_starvation_under_a_continuous_small_job_stream() -> None:
    small_user = uuid.UUID(int=11)
    large_user = uuid.UUID(int=12)
    large = candidate(
        large_user,
        80 * GIB,
        torrent_number=200,
        queued_at=NOW - timedelta(minutes=1),
    )
    ledger = SchedulerLedger()
    large_selected_on: int | None = None

    for round_number in range(1, 8):
        small = candidate(
            small_user,
            2 * GIB,
            torrent_number=200 + round_number,
            queued_at=NOW,
        )
        result = select_torrents(
            [small, large],
            policy=policy(max_active_global=1, max_active_per_user=1),
            now=NOW,
            active_global=0,
            active_by_user={},
            ledger=ledger,
        )
        ledger = result.ledger
        if result.selected[0].candidate.torrent_id == large.torrent_id:
            large_selected_on = round_number
            break

    assert large_selected_on is not None
    assert large_selected_on <= 4


@pytest.mark.parametrize("account_count", [1, 10, 25, 50, 100])
@pytest.mark.parametrize("active_slots", [1, 2])
def test_mixed_backlog_serves_every_account_with_one_or_two_slots(
    account_count: int,
    active_slots: int,
) -> None:
    users = [uuid.UUID(int=10_000 + index) for index in range(account_count)]
    queued: dict[uuid.UUID, SchedulerCandidate] = {}
    for user_index, user_id in enumerate(users):
        for size_index, size in enumerate((GIB, 20 * GIB, 80 * GIB)):
            item = candidate(
                user_id,
                size,
                torrent_number=20_000 + user_index * 3 + size_index,
                queued_at=NOW - timedelta(hours=user_index % 5),
                weight=1 + user_index % 3,
            )
            queued[item.torrent_id] = item

    ledger = SchedulerLedger()
    served: set[uuid.UUID] = set()
    rounds = 0
    configured = policy(
        max_active_global=active_slots,
        max_active_per_user=1,
    )
    while queued:
        result = select_torrents(
            list(queued.values()),
            policy=configured,
            now=NOW,
            active_global=0,
            active_by_user={},
            ledger=ledger,
        )
        assert 1 <= len(result.selected) <= active_slots
        ledger = result.ledger
        rounds += 1
        for decision in result.selected:
            served.add(decision.beneficiary_user_id)
            queued.pop(decision.candidate.torrent_id)

    assert served == set(users)
    effective_slots = min(account_count, active_slots)
    assert rounds <= (account_count * 3 + effective_slots - 1) // effective_slots


def test_bounded_aging_bonus_promotes_an_old_large_torrent() -> None:
    fresh_user = uuid.UUID(int=21)
    waiting_user = uuid.UUID(int=22)
    fresh_small = candidate(fresh_user, GIB, torrent_number=301, queued_at=NOW)
    old_large = candidate(
        waiting_user,
        80 * GIB,
        torrent_number=302,
        queued_at=NOW - timedelta(hours=10),
    )

    result = select_torrents(
        [fresh_small, old_large],
        policy=policy(max_active_global=1),
        now=NOW,
        active_global=0,
        active_by_user={},
    )

    assert result.selected[0].candidate is old_large
    assert result.selected[0].base_cost == 4
    assert result.selected[0].charged_cost == 1


def test_global_and_per_user_limits_are_both_enforced() -> None:
    first_user = uuid.UUID(int=31)
    second_user = uuid.UUID(int=32)
    jobs = [
        candidate(first_user, GIB, torrent_number=401),
        candidate(first_user, GIB, torrent_number=402),
        candidate(second_user, GIB, torrent_number=403),
    ]

    result = select_torrents(
        jobs,
        policy=policy(max_active_global=3, max_active_per_user=1),
        now=NOW,
        active_global=1,
        active_by_user={first_user: 0, second_user: 0},
    )

    assert len(result.selected) == 2
    assert {decision.candidate.user_id for decision in result.selected} == {
        first_user,
        second_user,
    }
    assert result.capacity_remaining == 0


def test_existing_per_user_activity_defers_only_that_users_jobs() -> None:
    saturated_user = uuid.UUID(int=41)
    available_user = uuid.UUID(int=42)
    saturated = candidate(saturated_user, GIB, torrent_number=501)
    available = candidate(available_user, GIB, torrent_number=502)

    result = select_torrents(
        [saturated, available],
        policy=policy(max_active_per_user=1),
        now=NOW,
        active_global=1,
        active_by_user={saturated_user: 1},
    )

    assert selected_ids(result) == [available.torrent_id]


def test_user_weight_increases_share_without_absolute_priority() -> None:
    free_user = uuid.UUID(int=51)
    premium_user = uuid.UUID(int=52)
    jobs = [candidate(free_user, GIB, torrent_number=600 + index) for index in range(1, 9)] + [
        candidate(premium_user, GIB, torrent_number=700 + index, weight=3) for index in range(1, 9)
    ]

    result = select_torrents(
        jobs,
        policy=policy(max_active_global=8, max_active_per_user=8),
        now=NOW,
        active_global=0,
        active_by_user={},
    )
    selected_users = [decision.candidate.user_id for decision in result.selected]

    assert selected_users.count(premium_user) > selected_users.count(free_user)
    assert free_user in selected_users


def test_shared_torrent_charge_rotates_deterministically_across_restarts() -> None:
    first_user = uuid.UUID(int=53)
    second_user = uuid.UUID(int=54)
    shared = candidate(
        first_user,
        GIB,
        torrent_number=750,
        beneficiaries=(second_user,),
    )
    configured = policy(max_active_global=1, max_active_per_user=1)
    ledger = SchedulerLedger()
    charged: list[uuid.UUID] = []

    for _ in range(4):
        result = select_torrents(
            [shared],
            policy=configured,
            now=NOW,
            active_global=0,
            active_by_user={},
            ledger=ledger,
        )
        charged.append(result.selected[0].beneficiary_user_id)
        ledger = result.ledger

    restarted = select_torrents(
        [shared],
        policy=configured,
        now=NOW,
        active_global=0,
        active_by_user={},
        ledger=ledger,
    )

    assert charged == [first_user, second_user, first_user, second_user]
    assert restarted.selected[0].beneficiary_user_id == first_user
    assert all(
        decision.candidate.torrent_id == shared.torrent_id for decision in restarted.selected
    )


def test_shared_torrents_use_alternate_owner_when_a_cap_is_reached() -> None:
    first_user = uuid.UUID(int=55)
    second_user = uuid.UUID(int=56)
    jobs = [
        candidate(first_user, GIB, torrent_number=760 + index, beneficiaries=(second_user,))
        for index in range(2)
    ]

    result = select_torrents(
        jobs,
        policy=policy(max_active_global=2, max_active_per_user=1),
        now=NOW,
        active_global=0,
        active_by_user={},
    )

    assert {decision.beneficiary_user_id for decision in result.selected} == {
        first_user,
        second_user,
    }
    assert len({decision.candidate.torrent_id for decision in result.selected}) == 2


def test_shared_torrent_beneficiaries_support_distinct_future_weights() -> None:
    free_user = uuid.UUID(int=57)
    premium_user = uuid.UUID(int=58)
    jobs = [
        candidate(
            free_user,
            GIB,
            torrent_number=770 + index,
            beneficiaries=(premium_user,),
            beneficiary_weights={free_user: 1, premium_user: 3},
        )
        for index in range(4)
    ]

    result = select_torrents(
        jobs,
        policy=policy(max_active_global=4, max_active_per_user=4),
        now=NOW,
        active_global=0,
        active_by_user={},
    )
    charged = [decision.beneficiary_user_id for decision in result.selected]

    assert charged.count(premium_user) == 3
    assert charged.count(free_user) == 1


def test_stalled_torrents_do_not_consume_capacity() -> None:
    user_id = uuid.UUID(int=61)
    stalled = candidate(user_id, GIB, torrent_number=801, stalled=True)
    healthy = candidate(user_id, GIB, torrent_number=802)

    result = select_torrents(
        [stalled, healthy],
        policy=policy(max_active_global=2),
        now=NOW,
        active_global=0,
        active_by_user={},
    )

    assert selected_ids(result) == [healthy.torrent_id]
    assert result.stalled_torrent_ids == (stalled.torrent_id,)
    assert result.capacity_remaining == 1


def test_selection_is_deterministic_regardless_of_input_order() -> None:
    users = [uuid.UUID(int=value) for value in (71, 72, 73)]
    jobs = [
        candidate(users[index % 3], (index + 1) * GIB, torrent_number=900 + index)
        for index in range(6)
    ]
    shuffled = list(jobs)
    random.Random(42).shuffle(shuffled)
    configured = policy(max_active_global=5)

    first = select_torrents(jobs, policy=configured, now=NOW, active_global=0, active_by_user={})
    second = select_torrents(
        shuffled, policy=configured, now=NOW, active_global=0, active_by_user={}
    )

    assert selected_ids(first) == selected_ids(second)
    assert first.ledger == second.ledger


def test_duplicate_physical_torrent_and_inconsistent_user_weight_are_rejected() -> None:
    user_id = uuid.UUID(int=81)
    duplicate_id = uuid.UUID(int=1001)
    duplicate = SchedulerCandidate(
        torrent_id=duplicate_id,
        user_id=user_id,
        remaining_bytes=GIB,
        queued_at=NOW,
    )
    conflicting_weight = SchedulerCandidate(
        torrent_id=uuid.UUID(int=1002),
        user_id=user_id,
        remaining_bytes=GIB,
        queued_at=NOW,
        user_weight=2,
    )
    configured = policy()

    with pytest.raises(ValueError, match="physical torrent"):
        select_torrents(
            [duplicate, duplicate],
            policy=configured,
            now=NOW,
            active_global=0,
            active_by_user={},
        )
    with pytest.raises(ValueError, match="same weight"):
        select_torrents(
            [duplicate, conflicting_weight],
            policy=configured,
            now=NOW,
            active_global=0,
            active_by_user={},
        )


@pytest.mark.parametrize(
    "queued_at",
    [datetime(2026, 8, 21, 20), datetime.min],
)
def test_candidate_rejects_timezone_naive_queue_time(queued_at: datetime) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        candidate(uuid.UUID(int=91), GIB, torrent_number=1101, queued_at=queued_at)
