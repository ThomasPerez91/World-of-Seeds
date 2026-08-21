from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Executable

from app.coordination import RedisCoordinator
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
    ManagedTorrent,
    ManagedTorrentState,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
    TrackerActivityOutcome,
    TrackerActivityType,
)
from app.storage import SharedContentStore, SharedContentStoreError
from app.torrents import (
    TorrentManifestError,
    TorrentValidationError,
    record_tracker_activity,
    replace_torrent_manifest,
)

ADD_TORRENT_JOB = "ADD_TORRENT"
SYNC_TORRENT_JOB = "SYNC_TORRENT"
MAX_SYNC_BATCH = 200
SYNC_INTERVAL_OPTION = "WOS_QB_SYNC_INTERVAL_SECONDS"

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
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._session_factory = session_factory
        self._router = router
        self._payloads = payloads
        self._content = content
        self._clock = clock

    @property
    def handlers(self) -> Mapping[str, TorrentJobHandler]:
        return {
            ADD_TORRENT_JOB: self.add_torrent,
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
        async with self._session_factory() as session, session.begin():
            managed = await session.get(
                ManagedTorrent,
                snapshot.managed_torrent_id,
                with_for_update=True,
            )
            if managed is None:
                raise PermanentTorrentJobError("managed_torrent_missing")
            managed.state = ManagedTorrentState.DOWNLOADING
            managed.qb_state = "added"
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
            if torrent.state in {ManagedTorrentState.PURGE_PENDING, ManagedTorrentState.PURGED}:
                return
            torrent.state = state
            torrent.qb_state = safe_qb_state
            torrent.retry_at = None
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


def _database_timestamp(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value
