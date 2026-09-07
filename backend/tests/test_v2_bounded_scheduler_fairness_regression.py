from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.scheduler.weighted_fair import (
    SchedulerCandidate,
    SchedulerLedger,
    SchedulerPolicy,
    select_torrents,
)

NOW = datetime(2026, 9, 5, 18, tzinfo=UTC)
GIB = 1024**3


def _policy(*, slots: int = 1) -> SchedulerPolicy:
    return SchedulerPolicy(
        max_active_global=slots,
        max_active_per_user=1,
        small_torrent_bytes=10 * GIB,
        medium_torrent_bytes=50 * GIB,
        deficit_quantum=1,
        aging_interval_seconds=3600,
        aging_max_bonus=3,
    )


def _candidate(user_id: uuid.UUID, torrent_id: int) -> SchedulerCandidate:
    return SchedulerCandidate(
        torrent_id=uuid.UUID(int=torrent_id),
        user_id=user_id,
        remaining_bytes=GIB,
        queued_at=NOW,
    )


def _global_order(
    cursor_user_id: uuid.UUID | None,
    users: tuple[uuid.UUID, ...],
) -> tuple[uuid.UUID, ...]:
    if cursor_user_id is None:
        return ()
    ordered = sorted(users, key=str)
    cursor_key = str(cursor_user_id)
    return tuple(
        [user_id for user_id in ordered if str(user_id) > cursor_key]
        + [user_id for user_id in ordered if str(user_id) <= cursor_key]
    )


def test_missing_cursor_advances_global_user_ring_across_bounded_windows() -> None:
    """A rotating torrent window must retain fairness when the prior user is absent."""
    first = uuid.UUID(int=1)
    second = uuid.UUID(int=2)
    third = uuid.UUID(int=3)
    users = (first, second, third)
    candidates = {
        first: _candidate(first, 10),
        second: _candidate(second, 20),
        third: _candidate(third, 30),
    }
    windows = (
        (first, second),
        (second, third),
        (first, third),
    )

    ledger = SchedulerLedger()
    served: list[uuid.UUID] = []
    for window in windows:
        result = select_torrents(
            [candidates[user_id] for user_id in window],
            policy=_policy(),
            now=NOW,
            active_global=0,
            active_by_user={},
            ledger=ledger,
            fairness_user_order=_global_order(ledger.cursor_user_id, users),
            fairness_user_order_complete=ledger.cursor_user_id is not None,
        )
        assert len(result.selected) == 1
        served.append(result.selected[0].beneficiary_user_id)
        ledger = result.ledger

    assert served == [first, second, third]


def test_absent_next_global_user_is_not_skipped_by_fallback_work() -> None:
    """Regression for the PR review case where u2 disappears every other control window."""
    first = uuid.UUID(int=1)
    second = uuid.UUID(int=2)
    third = uuid.UUID(int=3)
    users = (first, second, third)
    candidates = {
        first: _candidate(first, 10),
        second: _candidate(second, 20),
        third: _candidate(third, 30),
    }
    windows = (
        (first, second, third),
        (first, third),
        (first, second, third),
    )

    ledger = SchedulerLedger()
    served: list[uuid.UUID] = []
    cursors: list[uuid.UUID | None] = []
    for window in windows:
        result = select_torrents(
            [candidates[user_id] for user_id in window],
            policy=_policy(),
            now=NOW,
            active_global=0,
            active_by_user={},
            ledger=ledger,
            fairness_user_order=_global_order(ledger.cursor_user_id, users),
            fairness_user_order_complete=ledger.cursor_user_id is not None,
        )
        assert len(result.selected) == 1
        served.append(result.selected[0].beneficiary_user_id)
        ledger = result.ledger
        cursors.append(ledger.cursor_user_id)

    assert served == [first, third, second]
    assert cursors == [first, first, second]


def test_incomplete_global_ring_never_advances_past_unproven_successors() -> None:
    first = uuid.UUID(int=1)
    second = uuid.UUID(int=2)
    third = uuid.UUID(int=3)
    candidates = {
        second: _candidate(second, 20),
        third: _candidate(third, 30),
    }

    result = select_torrents(
        list(candidates.values()),
        policy=_policy(slots=2),
        now=NOW,
        active_global=0,
        active_by_user={},
        ledger=SchedulerLedger(cursor_user_id=first),
        fairness_user_order=(second,),
        fairness_user_order_complete=False,
    )

    assert [decision.beneficiary_user_id for decision in result.selected] == [second, third]
    assert result.ledger.cursor_user_id == second
