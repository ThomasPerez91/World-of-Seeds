from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Executable

from app.coordination import RedisCoordinator, TorrentEventType, TorrentRealtimeEvent
from app.integrations.account_routing import AccountRoutingError, TorrentEffectRoute
from app.integrations.c411_v2 import (
    C411V2PayloadError,
    NewGreedyV2UnavailableError,
)
from app.integrations.http import IntegrationAuthenticationError
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2ManagedIdentity,
    QBittorrentV2OwnershipError,
    QBittorrentV2RejectedError,
    QBittorrentV2TorrentSnapshot,
    QBittorrentV2TransientError,
)
from app.jobs.torrent_payloads import TorrentPayloadStore, TorrentPayloadStoreError
from app.jobs.worker import (
    PermanentTorrentJobError,
    TorrentJobHandler,
    TorrentJobSnapshot,
    TransientTorrentJobError,
)
from app.models import (
    DatabaseOption,
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    StorageLedger,
    TorrentFile,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    TrackerActivityOutcome,
    TrackerActivityType,
)
from app.storage import SharedContentStore, SharedContentStoreError
from app.torrents import (
    PURGE_TORRENT_JOB,
    TorrentManifestError,
    TorrentValidationError,
    record_tracker_activity,
    replace_torrent_manifest,
)

ADD_TORRENT_JOB = "ADD_TORRENT"
SYNC_TORRENT_JOB = "SYNC_TORRENT"
MAX_SYNC_BATCH = 200
SYNC_INTERVAL_OPTION = "WOS_QB_SYNC_INTERVAL_SECONDS"
STALL_TIMEOUT = timedelta(seconds=60)
STALL_COOLDOWNS = (
    timedelta(minutes=3),
    timedelta(minutes=5),
    timedelta(minutes=10),
)
STALL_EVALUATED_QB_STATES = frozenset({"downloading", "forceddl", "metadl", "stalleddl"})

logger = logging.getLogger(__name__)

type Clock = Callable[[], datetime]


class _TorrentRouter(Protocol):
    async def resolve(
        self,
        managed_torrent_id: uuid.UUID,
        info_hash: str,
    ) -> TorrentEffectRoute: ...


class TorrentEffectHandlers:
    """Concrete, replay-safe worker effects for managed torrent jobs."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        router: _TorrentRouter,
        payloads: TorrentPayloadStore,
        content: SharedContentStore,
        *,
        redis: RedisCoordinator | None = None,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._router = router
        self._payloads = payloads
        self._content = content
        self._redis = redis or RedisCoordinator.unconfigured()
        self._clock = clock

    @property
    def handlers(self) -> Mapping[str, TorrentJobHandler]:
        return {
            ADD_TORRENT_JOB: self.add_torrent,
            PURGE_TORRENT_JOB: self.purge_torrent,
            SYNC_TORRENT_JOB: self.sync_torrent,
        }

    async def add_torrent(self, snapshot: TorrentJobSnapshot) -> None:
        torrent, should_add = await self._mark_adding(snapshot)
        if not should_add:
            try:
                await asyncio.to_thread(self._payloads.remove, torrent.storage_key)
            except TorrentPayloadStoreError:
                logger.warning("torrent_worker_payload_cleanup_failed")
            return
        try:
            route = await self._router.resolve(torrent.id, torrent.info_hash)
        except AccountRoutingError as exc:
            raise PermanentTorrentJobError(
                "torrent_account_route_invalid",
                torrent_state=ManagedTorrentState.ERROR,
            ) from exc
        try:
            parsed = await asyncio.to_thread(self._payloads.read, torrent.storage_key)
        except TorrentPayloadStoreError as exc:
            raise PermanentTorrentJobError(
                "torrent_payload_unavailable",
                torrent_state=ManagedTorrentState.ERROR,
            ) from exc
        if (
            parsed.info_hash != torrent.info_hash
            or parsed.name != torrent.name
            or parsed.total_size != torrent.total_size
        ):
            raise PermanentTorrentJobError(
                "torrent_payload_mismatch",
                torrent_state=ManagedTorrentState.ERROR,
            )

        try:
            async with self._session_factory() as session, session.begin():
                await replace_torrent_manifest(session, torrent.id, parsed.files, now=self._clock())
        except TorrentManifestError as exc:
            raise PermanentTorrentJobError(
                "torrent_manifest_invalid",
                torrent_state=ManagedTorrentState.ERROR,
            ) from exc

        try:
            await asyncio.to_thread(self._content.prepare, torrent.storage_key)
        except SharedContentStoreError as exc:
            raise PermanentTorrentJobError(
                "shared_storage_invalid",
                torrent_state=ManagedTorrentState.ERROR,
            ) from exc

        try:
            await route.adder.add_torrent(
                parsed.content,
                expected_info_hash=torrent.info_hash,
                storage_key=torrent.storage_key,
            )
        except (NewGreedyV2UnavailableError, QBittorrentV2TransientError) as exc:
            raise TransientTorrentJobError(
                "torrent_integration_unavailable",
                torrent_state=ManagedTorrentState.RETRY_WAIT,
            ) from exc
        except IntegrationAuthenticationError as exc:
            raise TransientTorrentJobError(
                "torrent_integration_authentication",
                torrent_state=ManagedTorrentState.RETRY_WAIT,
            ) from exc
        except (C411V2PayloadError, TorrentValidationError) as exc:
            raise PermanentTorrentJobError(
                "torrent_payload_invalid",
                torrent_state=ManagedTorrentState.ERROR,
            ) from exc
        except (QBittorrentV2OwnershipError, QBittorrentV2RejectedError) as exc:
            raise PermanentTorrentJobError(
                "torrent_add_rejected",
                torrent_state=ManagedTorrentState.ERROR,
            ) from exc

        now = self._clock()
        realtime_events: list[tuple[uuid.UUID, TorrentRealtimeEvent]] = []
        async with self._session_factory() as session, session.begin():
            managed = await session.get(
                ManagedTorrent,
                snapshot.managed_torrent_id,
                with_for_update=True,
            )
            if managed is None:
                raise PermanentTorrentJobError("managed_torrent_missing")
            if managed.state in {
                ManagedTorrentState.PURGE_PENDING,
                ManagedTorrentState.PURGING,
                ManagedTorrentState.PURGED,
            }:
                try:
                    self._payloads.remove(torrent.storage_key)
                except TorrentPayloadStoreError:
                    logger.warning("torrent_worker_payload_cleanup_failed")
                return
            managed.state = ManagedTorrentState.PAUSED
            managed.qb_state = "stoppeddl"
            managed.retry_at = None
            managed.updated_at = now
            requests = list(
                (
                    await session.scalars(
                        select(TorrentRequest)
                        .where(
                            TorrentRequest.managed_torrent_id == managed.id,
                            TorrentRequest.state == TorrentRequestState.REQUESTED,
                        )
                        .with_for_update()
                    )
                ).all()
            )
            for request in requests:
                request.state = TorrentRequestState.ACTIVE
                request.updated_at = now
                realtime_events.append(
                    (
                        request.user_id,
                        TorrentRealtimeEvent(TorrentEventType.PAUSED, request.id, now),
                    )
                )
            await record_tracker_activity(
                session,
                managed.id,
                event_key=snapshot.id,
                tracker_account_ref=route.tracker_account_ref,
                event_type=TrackerActivityType.PROXY_HEALTH,
                outcome=TrackerActivityOutcome.SUCCESS,
                diagnostic_code=None,
                occurred_at=now,
            )
        await self._publish_realtime_events(realtime_events)
        try:
            await asyncio.to_thread(self._payloads.remove, torrent.storage_key)
        except TorrentPayloadStoreError:
            logger.warning("torrent_worker_payload_cleanup_failed")

    async def sync_torrent(self, snapshot: TorrentJobSnapshot) -> None:
        async with self._session_factory() as session:
            torrent = await session.get(ManagedTorrent, snapshot.managed_torrent_id)
            if torrent is None:
                raise PermanentTorrentJobError("managed_torrent_missing")
            if torrent.state in {
                ManagedTorrentState.PENDING,
                ManagedTorrentState.PURGE_PENDING,
                ManagedTorrentState.PURGING,
                ManagedTorrentState.PURGED,
            }:
                return
            identity = QBittorrentV2ManagedIdentity(torrent.info_hash, torrent.storage_key)

        try:
            route = await self._router.resolve(torrent.id, torrent.info_hash)
        except AccountRoutingError as exc:
            raise PermanentTorrentJobError(
                "torrent_account_route_invalid",
                torrent_state=ManagedTorrentState.ERROR,
            ) from exc

        try:
            snapshots = await route.inspector.inspect_managed_torrents((identity,))
        except (QBittorrentV2TransientError, IntegrationAuthenticationError) as exc:
            raise TransientTorrentJobError("qbittorrent_sync_unavailable") from exc
        except QBittorrentV2OwnershipError as exc:
            raise PermanentTorrentJobError(
                "qbittorrent_ownership_conflict",
                torrent_state=ManagedTorrentState.ERROR,
            ) from exc
        if len(snapshots) != 1:
            raise TransientTorrentJobError("qbittorrent_sync_incomplete")
        await self._persist_snapshot(snapshot.managed_torrent_id, snapshots[0])

    async def purge_torrent(self, snapshot: TorrentJobSnapshot) -> None:
        now = self._clock()
        database_now = _database_timestamp(now)
        async with self._session_factory() as session, session.begin():
            torrent = await session.get(
                ManagedTorrent,
                snapshot.managed_torrent_id,
                with_for_update=True,
            )
            if torrent is None:
                raise PermanentTorrentJobError("managed_torrent_missing")
            if torrent.state is ManagedTorrentState.PURGED:
                return
            if torrent.state not in {
                ManagedTorrentState.PURGE_PENDING,
                ManagedTorrentState.PURGING,
            }:
                return
            active_requests = await session.scalar(
                select(func.count())
                .select_from(TorrentRequest)
                .where(
                    TorrentRequest.managed_torrent_id == torrent.id,
                    TorrentRequest.state.in_(
                        (
                            TorrentRequestState.REQUESTED,
                            TorrentRequestState.ACTIVE,
                            TorrentRequestState.READY,
                        )
                    ),
                )
            )
            if active_requests:
                torrent.state = (
                    ManagedTorrentState.READY
                    if torrent.progress >= 1
                    else ManagedTorrentState.DOWNLOADING
                )
                torrent.purge_after = None
                torrent.updated_at = now
                return
            if torrent.purge_after is None or _as_utc(torrent.purge_after) > _as_utc(now):
                raise TransientTorrentJobError("torrent_retention_active")
            active_leases = await session.scalar(
                select(func.count())
                .select_from(DownloadLease)
                .where(
                    DownloadLease.managed_torrent_id == torrent.id,
                    DownloadLease.expires_at > database_now,
                )
            )
            if active_leases:
                raise TransientTorrentJobError("torrent_download_active")
            torrent.state = ManagedTorrentState.PURGING
            torrent.updated_at = now
            identity = QBittorrentV2ManagedIdentity(torrent.info_hash, torrent.storage_key)
            storage_key = torrent.storage_key
            total_size = torrent.total_size

        try:
            route = await self._router.resolve(snapshot.managed_torrent_id, identity.info_hash)
            await route.inspector.remove_managed_torrent(identity)
            await asyncio.to_thread(self._content.purge, storage_key)
        except AccountRoutingError as exc:
            raise PermanentTorrentJobError("torrent_account_route_invalid") from exc
        except (QBittorrentV2TransientError, IntegrationAuthenticationError) as exc:
            raise TransientTorrentJobError("torrent_purge_integration_unavailable") from exc
        except QBittorrentV2OwnershipError as exc:
            raise PermanentTorrentJobError("qbittorrent_ownership_conflict") from exc
        except QBittorrentV2RejectedError as exc:
            raise PermanentTorrentJobError("torrent_purge_rejected") from exc
        except SharedContentStoreError as exc:
            raise TransientTorrentJobError("torrent_content_purge_unavailable") from exc

        async with self._session_factory() as session, session.begin():
            torrent = await session.get(
                ManagedTorrent,
                snapshot.managed_torrent_id,
                with_for_update=True,
            )
            if torrent is None:
                raise PermanentTorrentJobError("managed_torrent_missing")
            if torrent.state is ManagedTorrentState.PURGED:
                return
            if torrent.state is not ManagedTorrentState.PURGING:
                raise TransientTorrentJobError("torrent_purge_state_changed")
            active_requests = await session.scalar(
                select(func.count())
                .select_from(TorrentRequest)
                .where(
                    TorrentRequest.managed_torrent_id == torrent.id,
                    TorrentRequest.state.in_(
                        (
                            TorrentRequestState.REQUESTED,
                            TorrentRequestState.ACTIVE,
                            TorrentRequestState.READY,
                        )
                    ),
                )
            )
            active_leases = await session.scalar(
                select(func.count())
                .select_from(DownloadLease)
                .where(
                    DownloadLease.managed_torrent_id == torrent.id,
                    DownloadLease.expires_at > database_now,
                )
            )
            if active_requests or active_leases:
                raise TransientTorrentJobError("torrent_purge_race_detected")
            await session.execute(
                delete(TorrentFile).where(TorrentFile.managed_torrent_id == torrent.id)
            )
            ledger = await session.get(StorageLedger, 1, with_for_update=True)
            if ledger is not None:
                ledger.managed_bytes = max(0, ledger.managed_bytes - total_size)
                ledger.updated_at = now
            torrent.state = ManagedTorrentState.PURGED
            torrent.purge_after = None
            torrent.progress = 0
            torrent.qb_state = None
            torrent.retry_at = None
            torrent.manifest_version = 0
            torrent.manifest_checksum = None
            torrent.manifest_file_count = 0
            torrent.manifest_total_size = 0
            torrent.desired_active = False
            torrent.desired_priority = None
            torrent.last_progress_at = None
            torrent.last_downloaded_bytes = None
            torrent.stall_count = 0
            torrent.scheduler_retry_at = None
            torrent.updated_at = now

    async def _mark_adding(self, snapshot: TorrentJobSnapshot) -> tuple[ManagedTorrent, bool]:
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            torrent = await session.get(
                ManagedTorrent,
                snapshot.managed_torrent_id,
                with_for_update=True,
            )
            if torrent is None:
                raise PermanentTorrentJobError("managed_torrent_missing")
            if torrent.state in {
                ManagedTorrentState.DOWNLOADING,
                ManagedTorrentState.PAUSED,
                ManagedTorrentState.READY,
            }:
                session.expunge(torrent)
                return torrent, False
            if torrent.state not in {
                ManagedTorrentState.PENDING,
                ManagedTorrentState.ADDING,
                ManagedTorrentState.RETRY_WAIT,
            }:
                raise PermanentTorrentJobError("managed_torrent_state_invalid")
            torrent.state = ManagedTorrentState.ADDING
            torrent.retry_at = None
            torrent.updated_at = now
            await session.flush()
            session.expunge(torrent)
            return torrent, True

    async def _persist_snapshot(
        self,
        managed_torrent_id: uuid.UUID,
        snapshot: QBittorrentV2TorrentSnapshot,
    ) -> None:
        now = self._clock()
        state, safe_qb_state = _domain_state(snapshot)
        realtime_events: list[tuple[uuid.UUID, TorrentRealtimeEvent]] = []
        async with self._session_factory() as session, session.begin():
            torrent = await session.get(
                ManagedTorrent,
                managed_torrent_id,
                with_for_update=True,
            )
            if torrent is None:
                raise PermanentTorrentJobError("managed_torrent_missing")
            if torrent.info_hash != snapshot.info_hash:
                raise PermanentTorrentJobError("qbittorrent_sync_mismatch")
            if torrent.state in {
                ManagedTorrentState.PURGE_PENDING,
                ManagedTorrentState.PURGING,
                ManagedTorrentState.PURGED,
            }:
                return
            previous_qb_state = torrent.qb_state
            previous_state = torrent.state
            previous_retry_at = torrent.scheduler_retry_at
            previous_stall_count = torrent.stall_count
            state = _apply_stall_observation(
                torrent,
                snapshot,
                state=state,
                previous_qb_state=previous_qb_state,
                now=now,
            )
            torrent.qb_state = safe_qb_state
            torrent.state = state
            torrent.progress = snapshot.progress
            torrent.retry_at = None
            if state is ManagedTorrentState.READY:
                torrent.desired_active = False
                torrent.desired_priority = None
                torrent.desired_download_limit = 0
            torrent.updated_at = now
            requests = list(
                (
                    await session.scalars(
                        select(TorrentRequest)
                        .where(
                            TorrentRequest.managed_torrent_id == torrent.id,
                            TorrentRequest.state.in_(
                                (TorrentRequestState.REQUESTED, TorrentRequestState.ACTIVE)
                            ),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            for request in requests:
                if state is ManagedTorrentState.READY:
                    request.state = TorrentRequestState.READY
                    request.ready_at = now
                elif request.state is TorrentRequestState.REQUESTED:
                    request.state = TorrentRequestState.ACTIVE
                request.updated_at = now
            event_type = _snapshot_event_type(
                previous_state=previous_state,
                state=state,
                previous_retry_at=previous_retry_at,
                retry_at=torrent.scheduler_retry_at,
                previous_stall_count=previous_stall_count,
            )
            if event_type is not None:
                realtime_events.extend(
                    (
                        request.user_id,
                        TorrentRealtimeEvent(event_type, request.id, now),
                    )
                    for request in requests
                )
        await self._publish_realtime_events(realtime_events)

    async def _publish_realtime_events(
        self,
        events: list[tuple[uuid.UUID, TorrentRealtimeEvent]],
    ) -> None:
        for user_id, event in events:
            await self._redis.publish_torrent_event(user_id, event)


class TorrentSyncEnqueuer:
    """Periodically coalesce one durable sync job per active managed torrent."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis: RedisCoordinator,
        *,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis
        self._clock = clock
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            interval = 5
            try:
                interval, created = await self.enqueue_once()
                if created:
                    await self._redis.signal_job_available()
            except SQLAlchemyError:
                logger.warning("torrent_sync_enqueuer_database_unavailable")
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=interval)

    async def enqueue_once(self) -> tuple[int, int]:
        now = self._clock()
        database_now = _database_timestamp(now)
        async with self._session_factory() as session, session.begin():
            option = await session.get(DatabaseOption, SYNC_INTERVAL_OPTION)
            interval = (
                option.integer_value if option is not None and option.value_type == "integer" else 5
            )
            if interval is None or not 2 <= interval <= 300:
                raise RuntimeError("qBittorrent sync interval option is invalid")
            active_sync_exists = (
                select(TorrentJob.id)
                .where(
                    TorrentJob.managed_torrent_id == ManagedTorrent.id,
                    TorrentJob.job_type == SYNC_TORRENT_JOB,
                    TorrentJob.state.in_((TorrentJobState.QUEUED, TorrentJobState.RUNNING)),
                )
                .exists()
            )
            torrent_ids = tuple(
                (
                    await session.scalars(
                        select(ManagedTorrent.id)
                        .where(
                            ManagedTorrent.state.in_(
                                (
                                    ManagedTorrentState.DOWNLOADING,
                                    ManagedTorrentState.PAUSED,
                                    ManagedTorrentState.READY,
                                )
                            ),
                            ~active_sync_exists,
                        )
                        .order_by(ManagedTorrent.updated_at, ManagedTorrent.id)
                        .limit(MAX_SYNC_BATCH)
                    )
                ).all()
            )
            created = 0
            dialect = session.get_bind().dialect.name
            for managed_torrent_id in torrent_ids:
                values = {
                    "id": uuid.uuid4(),
                    "managed_torrent_id": managed_torrent_id,
                    "torrent_request_id": None,
                    "job_type": SYNC_TORRENT_JOB,
                    "idempotency_key": f"sync:{managed_torrent_id.hex}:{uuid.uuid4().hex}",
                    "state": TorrentJobState.QUEUED,
                    "attempt_count": 0,
                    "max_attempts": 5,
                    "available_at": database_now,
                    "created_at": database_now,
                    "updated_at": database_now,
                }
                if dialect == "postgresql":
                    statement: Executable = (
                        postgresql_insert(TorrentJob).values(**values).on_conflict_do_nothing()
                    )
                elif dialect == "sqlite":
                    statement = sqlite_insert(TorrentJob).values(**values).on_conflict_do_nothing()
                else:
                    raise RuntimeError("qBittorrent sync jobs require PostgreSQL or SQLite")
                result = cast(CursorResult[Any], await session.execute(statement))
                if result.rowcount == 1:
                    created += 1
        return interval, created


def _domain_state(
    snapshot: QBittorrentV2TorrentSnapshot,
) -> tuple[ManagedTorrentState, str]:
    normalized = snapshot.state.lower()
    if normalized in {"error", "missingfiles", "unknown"}:
        return ManagedTorrentState.ERROR, normalized
    if snapshot.progress >= 1 or normalized.endswith("up") or normalized == "uploading":
        return ManagedTorrentState.READY, normalized
    if normalized.startswith(("stopped", "paused")):
        return ManagedTorrentState.PAUSED, normalized
    if normalized in {
        "allocating",
        "checkingdl",
        "checkingresumedata",
        "downloading",
        "forceddl",
        "metadl",
        "moving",
        "queueddl",
        "stalleddl",
    }:
        return ManagedTorrentState.DOWNLOADING, normalized
    return ManagedTorrentState.ERROR, "unknown"


def _snapshot_event_type(
    *,
    previous_state: ManagedTorrentState,
    state: ManagedTorrentState,
    previous_retry_at: datetime | None,
    retry_at: datetime | None,
    previous_stall_count: int,
) -> TorrentEventType | None:
    if state is ManagedTorrentState.READY and previous_state is not ManagedTorrentState.READY:
        return TorrentEventType.READY
    if state is ManagedTorrentState.ERROR and previous_state is not ManagedTorrentState.ERROR:
        return TorrentEventType.FAILED
    if retry_at is not None and retry_at != previous_retry_at:
        return TorrentEventType.STALLED
    if state is ManagedTorrentState.PAUSED and previous_state is not ManagedTorrentState.PAUSED:
        return TorrentEventType.PAUSED
    if state is ManagedTorrentState.DOWNLOADING and previous_state is not state:
        if previous_state in {ManagedTorrentState.PAUSED, ManagedTorrentState.RETRY_WAIT} or (
            previous_stall_count > 0
        ):
            return TorrentEventType.RESUMED
        return TorrentEventType.STARTED
    return None


def _apply_stall_observation(
    torrent: ManagedTorrent,
    snapshot: QBittorrentV2TorrentSnapshot,
    *,
    state: ManagedTorrentState,
    previous_qb_state: str | None,
    now: datetime,
) -> ManagedTorrentState:
    previous_downloaded = torrent.last_downloaded_bytes
    useful_progress = snapshot.progress > torrent.progress or (
        previous_downloaded is not None and snapshot.downloaded_bytes > previous_downloaded
    )
    observed_downloaded = max(previous_downloaded or 0, snapshot.downloaded_bytes)

    if state is ManagedTorrentState.READY:
        torrent.last_progress_at = now
        torrent.last_downloaded_bytes = observed_downloaded
        torrent.stall_count = 0
        torrent.scheduler_retry_at = None
        return state

    if useful_progress:
        torrent.last_progress_at = now
        torrent.last_downloaded_bytes = observed_downloaded
        torrent.stall_count = 0
        torrent.scheduler_retry_at = None
        return state

    if torrent.last_progress_at is None or previous_downloaded is None:
        torrent.last_progress_at = now
        torrent.last_downloaded_bytes = observed_downloaded
        return state

    retry_at = torrent.scheduler_retry_at
    if retry_at is not None and _as_utc(retry_at) > _as_utc(now):
        return ManagedTorrentState.PAUSED

    if snapshot.state.lower() not in STALL_EVALUATED_QB_STATES:
        return state

    if previous_qb_state is not None and previous_qb_state.lower().startswith(
        ("stopped", "paused")
    ):
        torrent.scheduler_retry_at = None
        torrent.last_progress_at = now
        torrent.last_downloaded_bytes = observed_downloaded
        return state

    if retry_at is not None:
        torrent.scheduler_retry_at = None
        torrent.last_progress_at = now
        torrent.last_downloaded_bytes = observed_downloaded
        return state

    if _as_utc(now) - _as_utc(torrent.last_progress_at) < STALL_TIMEOUT:
        return state

    torrent.stall_count += 1
    cooldown = STALL_COOLDOWNS[min(torrent.stall_count - 1, len(STALL_COOLDOWNS) - 1)]
    torrent.scheduler_retry_at = now + cooldown
    return ManagedTorrentState.PAUSED


def _database_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
