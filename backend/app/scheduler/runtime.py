from __future__ import annotations

import asyncio
import logging
import math
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.qbittorrent_v2 import (
    QBittorrentV2ControlResult,
    QBittorrentV2DesiredControl,
)
from app.models import ManagedTorrent, ManagedTorrentState, TorrentRequest, TorrentRequestState
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
            torrents = await self._load_control_set(session)
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
            generation = state.desired_generation + 1
            _persist_desired_controls(torrents, controls, generation=generation)
            state.desired_generation = generation
            await persist_scheduler_ledger(session, state, selection.ledger, now=now)

        control_result = await self._gateway.apply_managed_controls(controls)

        applied_at = self._clock()
        async with self._session_factory() as session, session.begin():
            await mark_scheduler_generation_applied(
                session,
                owner=self._scheduler_id,
                generation=generation,
                now=applied_at,
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
    async def _load_control_set(session: AsyncSession) -> tuple[ManagedTorrent, ...]:
        torrents = tuple(
            (
                await session.scalars(
                    select(ManagedTorrent)
                    .where(
                        ManagedTorrent.state.in_(
                            (ManagedTorrentState.DOWNLOADING, ManagedTorrentState.PAUSED)
                        )
                    )
                    .order_by(ManagedTorrent.created_at, ManagedTorrent.id)
                    .with_for_update()
                    .limit(MAX_SCHEDULER_CONTROL_SET + 1)
                )
            ).all()
        )
        if len(torrents) > MAX_SCHEDULER_CONTROL_SET:
            raise RuntimeError("scheduler control set exceeds the bounded limit")
        return torrents

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
                    .where(
                        TorrentRequest.managed_torrent_id.in_(torrent.id for torrent in torrents),
                        TorrentRequest.state.in_(
                            (TorrentRequestState.REQUESTED, TorrentRequestState.ACTIVE)
                        ),
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
    beneficiary: dict[uuid.UUID, TorrentRequest] = {}
    for request in requests:
        beneficiary.setdefault(request.managed_torrent_id, request)
    candidates: list[SchedulerCandidate] = []
    for torrent in torrents:
        candidate_request = beneficiary.get(torrent.id)
        if candidate_request is None:
            continue
        if torrent.scheduler_retry_at is not None and _utc(torrent.scheduler_retry_at) > _utc(now):
            continue
        remaining_bytes = _remaining_bytes(torrent.total_size, torrent.progress)
        if remaining_bytes is None or remaining_bytes <= 0:
            continue
        candidates.append(
            SchedulerCandidate(
                torrent_id=torrent.id,
                user_id=candidate_request.user_id,
                remaining_bytes=remaining_bytes,
                queued_at=_utc(candidate_request.created_at),
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
