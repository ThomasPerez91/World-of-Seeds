from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination import RedisCoordinator, TorrentEventType, TorrentRealtimeEvent
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2ControlResult,
    QBittorrentV2DesiredControl,
    QBittorrentV2RunState,
)
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    SchedulerState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.options import PostgresOptionsRegistry
from app.scheduler.persistence import (
    acquire_scheduler_lease,
    load_scheduler_ledger,
    mark_scheduler_generation_applied,
    persist_scheduler_ledger,
)
from app.scheduler.qbittorrent_control import (
    ManagedTorrentControlIdentity,
    build_qbittorrent_control_plan,
)
from app.scheduler.weighted_fair import SchedulerCandidate, SchedulerPolicy, select_torrents

MAX_SCHEDULER_CONTROL_SET = 200
logger = logging.getLogger(__name__)
type Clock = Callable[[], datetime]


class ManagedControlGateway(Protocol):
    async def apply_managed_controls(
        self, controls: Sequence[QBittorrentV2DesiredControl]
    ) -> QBittorrentV2ControlResult: ...


@dataclass(frozen=True, slots=True)
class SchedulerCycleResult:
    leader: bool
    generation: int | None
    selected_torrent_ids: tuple[uuid.UUID, ...]
    control_result: QBittorrentV2ControlResult | None


class SchedulerRuntime:
    """Durable singleton scheduler whose desired qB state survives process failure."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: ManagedControlGateway,
        *,
        scheduler_id: str,
        redis: RedisCoordinator | None = None,
        lease_ttl: timedelta = timedelta(seconds=30),
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        if not scheduler_id or len(scheduler_id) > 128:
            raise ValueError("scheduler ID must contain between 1 and 128 characters")
        if lease_ttl <= timedelta(0):
            raise ValueError("scheduler lease TTL must be positive")
        self._session_factory = session_factory
        self._gateway = gateway
        self._scheduler_id = scheduler_id
        self._redis = redis or RedisCoordinator.unconfigured()
        self._lease_ttl = lease_ttl
        self._clock = clock
        self._stop = asyncio.Event()
        self._options = PostgresOptionsRegistry()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            interval = await self._run_guarded_cycle()
            remaining = interval
            while remaining > 0 and not self._stop.is_set():
                wait_seconds = min(remaining, self._lease_ttl.total_seconds() / 3)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=wait_seconds)
                except TimeoutError:
                    remaining -= wait_seconds
                    if remaining > 0 and not await self._renew_lease():
                        break

    async def run_once(self) -> SchedulerCycleResult:
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            state = await acquire_scheduler_lease(
                session,
                owner=self._scheduler_id,
                now=now,
                ttl=self._lease_ttl,
            )
            if state is None:
                return SchedulerCycleResult(False, None, (), None)

            await self._options.initialize(session, now=now)
            options = await self._options.snapshot(session)
            policy = SchedulerPolicy.from_options(options)
            purge_stops = await self._load_purge_stops(session)
            torrents = await self._load_control_set(session, state)
            requests = await self._load_active_requests(session, torrents)
            ledger = await load_scheduler_ledger(session, state)
            candidates = _scheduler_candidates(torrents, requests, now=now)
            selection = select_torrents(
                candidates,
                policy=policy,
                now=now,
                active_global=0,
                active_by_user={},
                ledger=ledger,
            )
            identities = tuple(
                ManagedTorrentControlIdentity(
                    torrent_id=torrent.id,
                    info_hash=torrent.info_hash,
                    storage_key=torrent.storage_key,
                    qbittorrent_account_ref=torrent.qbittorrent_account_ref,
                )
                for torrent in torrents
            )
            controls = build_qbittorrent_control_plan(selection, identities, options=options)
            purge_controls = tuple(
                QBittorrentV2DesiredControl(
                    info_hash=torrent.info_hash,
                    storage_key=torrent.storage_key,
                    run_state=QBittorrentV2RunState.STOPPED,
                    download_limit_bytes_per_second=0,
                    qbittorrent_account_ref=torrent.qbittorrent_account_ref,
                )
                for torrent in purge_stops
            )
            generation = state.desired_generation + 1
            previous_active = {torrent.id: torrent.desired_active for torrent in torrents}
            purge_stop_generations = {
                torrent.id: torrent.lifecycle_generation for torrent in purge_stops
            }
            _persist_desired_controls(torrents, controls, generation=generation)
            realtime_targets = _control_event_targets(torrents, requests, previous_active)
            state.desired_generation = generation
            await persist_scheduler_ledger(session, state, selection.ledger, now=now)

        purge_control_result = (
            await self._gateway.apply_managed_controls(purge_controls)
            if purge_controls
            else QBittorrentV2ControlResult((), (), (), ())
        )
        scheduled_control_result = await self._gateway.apply_managed_controls(controls)
        control_result = _merge_control_results(purge_control_result, scheduled_control_result)

        applied_at = self._clock()
        async with self._session_factory() as session, session.begin():
            await mark_scheduler_generation_applied(
                session,
                owner=self._scheduler_id,
                generation=generation,
                now=applied_at,
            )
            if purge_stop_generations:
                stopped = tuple(
                    (
                        await session.scalars(
                            select(ManagedTorrent)
                            .where(
                                ManagedTorrent.id.in_(purge_stop_generations),
                                ManagedTorrent.state == ManagedTorrentState.PURGE_PENDING,
                                ManagedTorrent.purge_stop_pending.is_(True),
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                for torrent in stopped:
                    if torrent.lifecycle_generation == purge_stop_generations[torrent.id]:
                        torrent.purge_stop_pending = False
                        torrent.updated_at = applied_at
        for user_id, request_id, event_type in realtime_targets:
            await self._redis.publish_torrent_event(
                user_id,
                TorrentRealtimeEvent(event_type, request_id, applied_at),
            )
        return SchedulerCycleResult(
            leader=True,
            generation=generation,
            selected_torrent_ids=tuple(
                decision.candidate.torrent_id for decision in selection.selected
            ),
            control_result=control_result,
        )

    async def _run_guarded_cycle(self) -> float:
        interval = 5.0
        try:
            result = await self.run_once()
            if result.leader:
                async with self._session_factory() as session:
                    options = await self._options.snapshot(session)
                configured = options["WOS_QB_SYNC_INTERVAL_SECONDS"]
                if type(configured) is int:
                    interval = float(configured)
        except Exception:
            # The durable desired generation remains unapplied and is retried by this process
            # or by the next lease owner. Runtime logging is added with observability in V2-27.
            logger.warning("scheduler_cycle_failed")
            interval = min(5.0, self._lease_ttl.total_seconds())
        return interval

    async def _renew_lease(self) -> bool:
        try:
            async with self._session_factory() as session, session.begin():
                state = await acquire_scheduler_lease(
                    session,
                    owner=self._scheduler_id,
                    now=self._clock(),
                    ttl=self._lease_ttl,
                )
            return state is not None
        except Exception:
            logger.warning("scheduler_lease_renewal_failed")
            return False

    @staticmethod
    async def _load_purge_stops(session: AsyncSession) -> tuple[ManagedTorrent, ...]:
        return tuple(
            (
                await session.scalars(
                    select(ManagedTorrent)
                    .where(
                        ManagedTorrent.state == ManagedTorrentState.PURGE_PENDING,
                        ManagedTorrent.purge_stop_pending.is_(True),
                    )
                    .order_by(ManagedTorrent.updated_at, ManagedTorrent.id)
                    .with_for_update(skip_locked=True)
                    .limit(MAX_SCHEDULER_CONTROL_SET)
                )
            ).all()
        )

    @staticmethod
    async def _load_control_set(
        session: AsyncSession,
        state: SchedulerState,
    ) -> tuple[ManagedTorrent, ...]:
        active = tuple(
            (
                await session.scalars(
                    select(ManagedTorrent)
                    .where(
                        ManagedTorrent.state.in_(
                            (ManagedTorrentState.DOWNLOADING, ManagedTorrentState.PAUSED)
                        ),
                        ManagedTorrent.desired_active.is_(True),
                    )
                    .order_by(ManagedTorrent.desired_priority, ManagedTorrent.id)
                    .with_for_update()
                    .limit(MAX_SCHEDULER_CONTROL_SET + 1)
                )
            ).all()
        )
        if len(active) > MAX_SCHEDULER_CONTROL_SET:
            raise RuntimeError("active scheduler control set exceeds the bounded limit")
        capacity = MAX_SCHEDULER_CONTROL_SET - len(active)
        if capacity == 0:
            return active

        base = (
            select(ManagedTorrent)
            .where(
                ManagedTorrent.state.in_(
                    (ManagedTorrentState.DOWNLOADING, ManagedTorrentState.PAUSED)
                ),
                ManagedTorrent.desired_active.is_(False),
            )
            .order_by(ManagedTorrent.created_at, ManagedTorrent.id)
            .with_for_update()
        )
        after_cursor = base
        if state.scan_cursor_created_at is not None and state.scan_cursor_id is not None:
            after_cursor = after_cursor.where(
                or_(
                    ManagedTorrent.created_at > state.scan_cursor_created_at,
                    and_(
                        ManagedTorrent.created_at == state.scan_cursor_created_at,
                        ManagedTorrent.id > state.scan_cursor_id,
                    ),
                )
            )
        window = list((await session.scalars(after_cursor.limit(capacity))).all())
        if len(window) < capacity and state.scan_cursor_id is not None:
            selected_ids = [torrent.id for torrent in window]
            wrapped = base
            if selected_ids:
                wrapped = wrapped.where(ManagedTorrent.id.not_in(selected_ids))
            window.extend((await session.scalars(wrapped.limit(capacity - len(window)))).all())
        if window:
            state.scan_cursor_created_at = window[-1].created_at
            state.scan_cursor_id = window[-1].id
        else:
            state.scan_cursor_created_at = None
            state.scan_cursor_id = None
        return (*active, *window)

    @staticmethod
    async def _load_active_requests(
        session: AsyncSession,
        torrents: Sequence[ManagedTorrent],
    ) -> tuple[TorrentRequest, ...]:
        if not torrents:
            return ()
        return tuple(
            (
                await session.scalars(
                    select(TorrentRequest)
                    .join(User, User.id == TorrentRequest.user_id)
                    .where(
                        TorrentRequest.managed_torrent_id.in_(torrent.id for torrent in torrents),
                        TorrentRequest.state.in_(
                            (TorrentRequestState.REQUESTED, TorrentRequestState.ACTIVE)
                        ),
                        User.is_active.is_(True),
                        User.deleted_at.is_(None),
                    )
                    .order_by(
                        TorrentRequest.managed_torrent_id,
                        TorrentRequest.created_at,
                        TorrentRequest.user_id,
                    )
                )
            ).all()
        )


def _scheduler_candidates(
    torrents: Sequence[ManagedTorrent],
    requests: Sequence[TorrentRequest],
    *,
    now: datetime,
) -> tuple[SchedulerCandidate, ...]:
    requests_by_torrent: dict[uuid.UUID, list[TorrentRequest]] = {}
    for request in requests:
        requests_by_torrent.setdefault(request.managed_torrent_id, []).append(request)
    candidates: list[SchedulerCandidate] = []
    for torrent in torrents:
        torrent_requests = requests_by_torrent.get(torrent.id)
        if not torrent_requests:
            continue
        if torrent.scheduler_retry_at is not None and _utc(torrent.scheduler_retry_at) > _utc(now):
            continue
        remaining_bytes = _remaining_bytes(torrent.total_size, torrent.progress)
        if remaining_bytes is None or remaining_bytes <= 0:
            continue
        ordered_user_ids = tuple(dict.fromkeys(request.user_id for request in torrent_requests))
        candidates.append(
            SchedulerCandidate(
                torrent_id=torrent.id,
                user_id=ordered_user_ids[0],
                beneficiary_user_ids=ordered_user_ids[1:],
                remaining_bytes=remaining_bytes,
                queued_at=min(_utc(request.created_at) for request in torrent_requests),
            )
        )
    return tuple(candidates)


def _remaining_bytes(total_size: int, progress: float | None) -> int | None:
    """Return a conservative bounded cost for an incomplete torrent."""
    if total_size <= 0:
        return None
    if progress is None or not math.isfinite(progress):
        return total_size
    if progress <= 0:
        return total_size
    if progress >= 1:
        return 0
    return max(1, min(total_size, total_size - math.floor(total_size * progress)))


def _merge_control_results(
    first: QBittorrentV2ControlResult,
    second: QBittorrentV2ControlResult,
) -> QBittorrentV2ControlResult:
    return QBittorrentV2ControlResult(
        started=(*first.started, *second.started),
        stopped=(*first.stopped, *second.stopped),
        limits_updated=(*first.limits_updated, *second.limits_updated),
        priorities_applied=(*first.priorities_applied, *second.priorities_applied),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _persist_desired_controls(
    torrents: Sequence[ManagedTorrent],
    controls: Sequence[QBittorrentV2DesiredControl],
    *,
    generation: int,
) -> None:
    by_hash = {control.info_hash: control for control in controls}
    active_rank = 0
    for torrent in torrents:
        control = by_hash[torrent.info_hash]
        active = control.run_state.value == "running"
        torrent.desired_active = active
        torrent.desired_priority = active_rank if active else None
        torrent.desired_download_limit = control.download_limit_bytes_per_second
        torrent.schedule_generation = generation
        if active:
            active_rank += 1


def _control_event_targets(
    torrents: Sequence[ManagedTorrent],
    requests: Sequence[TorrentRequest],
    previous_active: dict[uuid.UUID, bool],
) -> tuple[tuple[uuid.UUID, uuid.UUID, TorrentEventType], ...]:
    by_torrent = {torrent.id: torrent for torrent in torrents}
    targets: list[tuple[uuid.UUID, uuid.UUID, TorrentEventType]] = []
    for request in requests:
        torrent = by_torrent[request.managed_torrent_id]
        was_active = previous_active[torrent.id]
        if torrent.desired_active == was_active:
            continue
        if torrent.desired_active:
            event_type = (
                TorrentEventType.RESUMED if torrent.stall_count > 0 else TorrentEventType.STARTED
            )
        else:
            event_type = TorrentEventType.PAUSED
        targets.append((request.user_id, request.id, event_type))
    return tuple(targets)
