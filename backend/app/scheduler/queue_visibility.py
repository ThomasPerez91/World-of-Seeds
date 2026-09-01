from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentRequest,
    TorrentRequestState,
    User,
)

type TorrentQueueStatus = Literal["waiting", "downloading", "stalled", "cooldown"]


@dataclass(frozen=True, slots=True)
class TorrentQueueVisibility:
    """Secret-free indication derived from the scheduler's durable physical queue."""

    status: TorrentQueueStatus
    position_estimate: int | None = None
    total_estimate: int | None = None


def torrent_queue_status(
    torrent: ManagedTorrent,
    *,
    now: datetime,
) -> TorrentQueueStatus | None:
    """Classify one physical torrent without predicting a weighted-fair decision."""

    timestamp = _utc(now)
    if torrent.state not in {
        ManagedTorrentState.PENDING,
        ManagedTorrentState.ADDING,
        ManagedTorrentState.DOWNLOADING,
        ManagedTorrentState.PAUSED,
        ManagedTorrentState.RETRY_WAIT,
    }:
        return None
    if torrent.scheduler_retry_at is not None and _utc(torrent.scheduler_retry_at) > timestamp:
        return "cooldown"
    if (torrent.qb_state or "").lower().startswith("stalled"):
        return "stalled"
    if torrent.desired_active:
        return "downloading"
    return "waiting"


async def is_ranked_queue_member(
    session: AsyncSession,
    torrent: ManagedTorrent,
    *,
    now: datetime,
) -> bool:
    """Return whether one physical torrent belongs to the public ranked waiting set."""

    timestamp = _utc(now)
    if (
        torrent.state not in {ManagedTorrentState.DOWNLOADING, ManagedTorrentState.PAUSED}
        or torrent.desired_active
        or (torrent.scheduler_retry_at is not None and _utc(torrent.scheduler_retry_at) > timestamp)
    ):
        return False
    return bool(
        await session.scalar(
            select(TorrentRequest.id)
            .join(User, User.id == TorrentRequest.user_id)
            .where(
                TorrentRequest.managed_torrent_id == torrent.id,
                TorrentRequest.state.in_(
                    (TorrentRequestState.REQUESTED, TorrentRequestState.ACTIVE)
                ),
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .limit(1)
        )
    )


async def user_active_status_changes_ranked_queue(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    now: datetime,
) -> bool:
    """Detect whether toggling one owner changes any physical ranked membership."""

    owner_request = aliased(TorrentRequest)
    other_request = aliased(TorrentRequest)
    other_user = aliased(User)
    other_active_owner = (
        select(other_request.id)
        .join(other_user, other_user.id == other_request.user_id)
        .where(
            other_request.managed_torrent_id == ManagedTorrent.id,
            other_request.user_id != user_id,
            other_request.state.in_((TorrentRequestState.REQUESTED, TorrentRequestState.ACTIVE)),
            other_user.is_active.is_(True),
            other_user.deleted_at.is_(None),
        )
        .exists()
    )
    timestamp = _utc(now)
    database_now = (
        timestamp.replace(tzinfo=None) if session.get_bind().dialect.name == "sqlite" else timestamp
    )
    return bool(
        await session.scalar(
            select(ManagedTorrent.id)
            .join(owner_request, owner_request.managed_torrent_id == ManagedTorrent.id)
            .where(
                owner_request.user_id == user_id,
                owner_request.state.in_(
                    (TorrentRequestState.REQUESTED, TorrentRequestState.ACTIVE)
                ),
                ManagedTorrent.state.in_(
                    (ManagedTorrentState.DOWNLOADING, ManagedTorrentState.PAUSED)
                ),
                ManagedTorrent.desired_active.is_(False),
                or_(
                    ManagedTorrent.scheduler_retry_at.is_(None),
                    ManagedTorrent.scheduler_retry_at <= database_now,
                ),
                ~other_active_owner,
            )
            .limit(1)
        )
    )


async def load_torrent_queue_visibility(
    session: AsyncSession,
    torrents: Sequence[ManagedTorrent],
    *,
    now: datetime,
) -> Mapping[uuid.UUID, TorrentQueueVisibility]:
    """Load page-scoped visibility with one global, window-ranked queue query.

    The numeric estimate uses the scheduler's real eligible physical backlog and deterministic
    database scan order. It deliberately stays stable while the circular scan cursor advances and
    does not claim to be a FIFO admission order: weighted fairness, deficit, aging, size, cooldown,
    shared ownership and available slots remain decisions of the real scheduler.
    """

    timestamp = _utc(now)
    visibility = {
        torrent.id: TorrentQueueVisibility(status)
        for torrent in torrents
        if (status := torrent_queue_status(torrent, now=timestamp)) is not None
    }
    waiting_ids = {
        torrent.id
        for torrent in torrents
        if visibility.get(torrent.id) == TorrentQueueVisibility("waiting")
        and torrent.state in {ManagedTorrentState.DOWNLOADING, ManagedTorrentState.PAUSED}
    }
    if not waiting_ids:
        return visibility

    database_now = (
        timestamp.replace(tzinfo=None) if session.get_bind().dialect.name == "sqlite" else timestamp
    )
    has_active_owner = (
        select(TorrentRequest.id)
        .join(User, User.id == TorrentRequest.user_id)
        .where(
            TorrentRequest.managed_torrent_id == ManagedTorrent.id,
            TorrentRequest.state.in_((TorrentRequestState.REQUESTED, TorrentRequestState.ACTIVE)),
            User.is_active.is_(True),
            User.deleted_at.is_(None),
        )
        .exists()
    )
    ranked = (
        select(
            ManagedTorrent.id.label("managed_torrent_id"),
            func.row_number()
            .over(order_by=(ManagedTorrent.created_at, ManagedTorrent.id))
            .label("position_estimate"),
            func.count().over().label("total_estimate"),
        )
        .where(
            ManagedTorrent.state.in_((ManagedTorrentState.DOWNLOADING, ManagedTorrentState.PAUSED)),
            ManagedTorrent.desired_active.is_(False),
            or_(
                ManagedTorrent.scheduler_retry_at.is_(None),
                ManagedTorrent.scheduler_retry_at <= database_now,
            ),
            has_active_owner,
        )
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                ranked.c.managed_torrent_id,
                ranked.c.position_estimate,
                ranked.c.total_estimate,
            ).where(ranked.c.managed_torrent_id.in_(waiting_ids))
        )
    ).all()
    for managed_torrent_id, position_estimate, total_estimate in rows:
        visibility[managed_torrent_id] = TorrentQueueVisibility(
            "waiting",
            int(position_estimate),
            int(total_estimate),
        )
    return visibility


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
