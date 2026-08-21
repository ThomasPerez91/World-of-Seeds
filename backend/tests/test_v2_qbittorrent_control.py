from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.scheduler.qbittorrent_control import (
    ManagedTorrentControlIdentity,
    build_qbittorrent_control_plan,
)
from app.scheduler.weighted_fair import (
    SchedulerCandidate,
    SchedulerPolicy,
    SchedulerResult,
    select_torrents,
)

NOW = datetime(2026, 8, 21, tzinfo=UTC)
USER_ID = UUID("10000000-0000-0000-0000-000000000001")
TORRENT_A = UUID("20000000-0000-0000-0000-000000000001")
TORRENT_B = UUID("20000000-0000-0000-0000-000000000002")
TORRENT_C = UUID("20000000-0000-0000-0000-000000000003")


def _result() -> SchedulerResult:
    policy = SchedulerPolicy(
        max_active_global=2,
        max_active_per_user=2,
        small_torrent_bytes=10,
        medium_torrent_bytes=20,
        deficit_quantum=4,
        aging_interval_seconds=60,
        aging_max_bonus=3,
    )
    candidates = tuple(
        SchedulerCandidate(
            torrent_id=torrent_id,
            user_id=USER_ID,
            remaining_bytes=5,
            queued_at=NOW,
        )
        for torrent_id in (TORRENT_B, TORRENT_A, TORRENT_C)
    )
    return select_torrents(
        candidates,
        policy=policy,
        now=NOW,
        active_global=0,
        active_by_user={},
    )


def _identities() -> tuple[ManagedTorrentControlIdentity, ...]:
    return tuple(
        ManagedTorrentControlIdentity(
            torrent_id=torrent_id,
            info_hash=character * 40,
            storage_key=UUID(f"30000000-0000-0000-0000-00000000000{index}"),
        )
        for index, (torrent_id, character) in enumerate(
            ((TORRENT_A, "a"), (TORRENT_B, "b"), (TORRENT_C, "c")), start=1
        )
    )


def test_plan_preserves_scheduler_priority_and_distributes_global_limit() -> None:
    plan = build_qbittorrent_control_plan(
        _result(),
        _identities(),
        options={"WOS_QB_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL": 101},
    )

    assert [
        (control.info_hash, control.run_state, control.download_limit_bytes_per_second)
        for control in plan
    ] == [
        ("a" * 40, "running", 51),
        ("b" * 40, "running", 50),
        ("c" * 40, "stopped", 0),
    ]


def test_plan_uses_qb_unlimited_value_when_global_limit_is_disabled() -> None:
    plan = build_qbittorrent_control_plan(
        _result(),
        _identities(),
        options={"WOS_QB_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL": 0},
    )

    assert [control.download_limit_bytes_per_second for control in plan] == [0, 0, 0]


@pytest.mark.parametrize("limit", [-1, 10_000_000_001, True, "100"])
def test_plan_rejects_invalid_global_limit(limit: object) -> None:
    with pytest.raises(ValueError):
        build_qbittorrent_control_plan(
            _result(),
            _identities(),
            options={"WOS_QB_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL": limit},  # type: ignore[dict-item]
        )
