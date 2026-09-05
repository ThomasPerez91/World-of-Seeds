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


def _policy() -> SchedulerPolicy:
    return SchedulerPolicy(
        max_active_global=1,
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


def test_missing_cursor_advances_stable_user_ring_across_bounded_windows() -> None:
    """A rotating DB window must not reset fairness when the prior user is temporarily absent."""
    first = uuid.UUID(int=1)
    second = uuid.UUID(int=2)
    third = uuid.UUID(int=3)
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
        )
        assert len(result.selected) == 1
        served.append(result.selected[0].beneficiary_user_id)
        ledger = result.ledger

    assert served == [first, second, third]
