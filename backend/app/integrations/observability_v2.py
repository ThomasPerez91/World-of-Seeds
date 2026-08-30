from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Literal

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.account_routing import DeploymentAccountSpec
from app.integrations.c411_v2 import NewGreedyV2Gateway
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2Gateway,
    QBittorrentV2InventoryItem,
)
from app.integrations.types import ExternalServicesSnapshot, ServiceProbe
from app.models import (
    IntegrationServiceHealth,
    IntegrationServiceState,
    QBittorrentInventoryItem,
    QBittorrentInventorySnapshot,
)

INVENTORY_PAGE_SIZE = 200
MAX_ACCOUNT_INVENTORY = 1_000
SNAPSHOT_RETENTION = timedelta(hours=1)
DEFAULT_STALE_AFTER = timedelta(seconds=45)
type IntegrationServiceName = Literal["newgreedy", "qbittorrent"]
type StoredProbe = tuple[IntegrationServiceState, int, str | None]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def load_v2_external_services_snapshot(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> ExternalServicesSnapshot:
    """Aggregate worker-published, secret-free health for every configured account."""

    timestamp = now or datetime.now(UTC)
    rows = tuple(
        (
            await session.execute(
                select(
                    IntegrationServiceHealth.service,
                    IntegrationServiceHealth.account_ref,
                    IntegrationServiceHealth.observation_set,
                    IntegrationServiceHealth.account_count,
                    IntegrationServiceHealth.state,
                    IntegrationServiceHealth.latency_ms,
                    IntegrationServiceHealth.error_code,
                    IntegrationServiceHealth.checked_at,
                )
            )
        ).all()
    )
    await session.rollback()
    by_service = {
        service: tuple(row for row in rows if row.service == service)
        for service in ("newgreedy", "qbittorrent")
    }
    newgreedy_refs = {row.account_ref for row in by_service["newgreedy"]}
    qbittorrent_refs = {row.account_ref for row in by_service["qbittorrent"]}
    observation_sets = {row.observation_set for row in rows}
    account_counts = {row.account_count for row in rows}
    expected_count = next(iter(account_counts)) if len(account_counts) == 1 else 0
    registry_complete = (
        len(observation_sets) == 1
        and len(account_counts) == 1
        and expected_count > 0
        and len(rows) == expected_count * 2
        and len(newgreedy_refs) == expected_count
        and newgreedy_refs == qbittorrent_refs
    )

    def aggregate(service: IntegrationServiceName) -> ServiceProbe:
        service_rows = by_service[service]
        if not service_rows or not registry_complete:
            return ServiceProbe(service=service, state="unavailable", error_code="health_missing")
        stale = any(timestamp - _utc(row.checked_at) > stale_after for row in service_rows)
        failed = next(
            (row for row in service_rows if row.state is IntegrationServiceState.UNAVAILABLE),
            None,
        )
        if stale:
            return ServiceProbe(service=service, state="unavailable", error_code="health_stale")
        if failed is not None:
            return ServiceProbe(
                service=service,
                state="unavailable",
                latency_ms=failed.latency_ms,
                error_code=failed.error_code or "probe_failed",
            )
        latencies = [row.latency_ms for row in service_rows if row.latency_ms is not None]
        return ServiceProbe(
            service=service,
            state="healthy",
            latency_ms=max(latencies) if latencies else None,
        )

    checked_at = min((_utc(row.checked_at) for row in rows), default=timestamp)
    return ExternalServicesSnapshot(
        checked_at=checked_at,
        newgreedy=aggregate("newgreedy"),
        qbittorrent=aggregate("qbittorrent"),
    )


class V2IntegrationObservabilityPublisher:
    """Scheduler-side publisher for API-visible health and read-only qB inventories."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        client: httpx.AsyncClient,
        specs: Sequence[DeploymentAccountSpec],
        *,
        data_root: Path,
        interval: timedelta = timedelta(seconds=10),
    ) -> None:
        if not specs:
            raise ValueError("integration observability requires at least one account")
        if interval <= timedelta(0):
            raise ValueError("integration observability interval must be positive")
        self._session_factory = session_factory
        self._client = client
        self._specs = tuple(specs)
        self._data_root = data_root
        self._interval = interval
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.refresh_once()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval.total_seconds())

    async def refresh_once(self) -> None:
        observation_set = uuid.uuid4()
        for spec in self._specs:
            await self._refresh_account(spec, observation_set=observation_set)
        account_refs = tuple(spec.qbittorrent_account_ref for spec in self._specs)
        async with self._session_factory() as session, session.begin():
            await session.execute(
                delete(IntegrationServiceHealth).where(
                    IntegrationServiceHealth.observation_set != observation_set
                )
            )
            await session.execute(
                delete(QBittorrentInventorySnapshot).where(
                    QBittorrentInventorySnapshot.account_ref.not_in(account_refs)
                )
            )

    async def _refresh_account(
        self,
        spec: DeploymentAccountSpec,
        *,
        observation_set: uuid.UUID,
    ) -> None:
        checked_at = datetime.now(UTC)
        newgreedy_started = perf_counter()
        try:
            await NewGreedyV2Gateway(self._client, spec.newgreedy_url).require_ready()
            newgreedy: StoredProbe = (
                IntegrationServiceState.HEALTHY,
                _milliseconds(newgreedy_started),
                None,
            )
        except Exception:
            newgreedy = (
                IntegrationServiceState.UNAVAILABLE,
                _milliseconds(newgreedy_started),
                "probe_failed",
            )

        qb_started = perf_counter()
        inventory: tuple[QBittorrentV2InventoryItem, ...] | None = None
        inventory_truncated = False
        try:
            gateway = QBittorrentV2Gateway(
                self._client,
                spec.qbittorrent_url,
                spec.qbittorrent_username,
                spec.qbittorrent_password.get_secret_value(),
                data_root=self._data_root,
            )
            items: list[QBittorrentV2InventoryItem] = []
            for offset in range(0, MAX_ACCOUNT_INVENTORY, INVENTORY_PAGE_SIZE):
                page = await gateway.inventory_torrents(
                    limit=INVENTORY_PAGE_SIZE,
                    offset=offset,
                )
                items.extend(page.items)
                if not page.truncated:
                    break
            else:
                inventory_truncated = True
            if len(items) >= MAX_ACCOUNT_INVENTORY and page.truncated:
                inventory_truncated = True
            inventory = tuple(items[:MAX_ACCOUNT_INVENTORY])
            qbittorrent: StoredProbe = (
                IntegrationServiceState.HEALTHY,
                _milliseconds(qb_started),
                None,
            )
        except Exception:
            qbittorrent = (
                IntegrationServiceState.UNAVAILABLE,
                _milliseconds(qb_started),
                "probe_failed",
            )

        async with self._session_factory() as session, session.begin():
            await self._store_health(
                session,
                "newgreedy",
                spec.qbittorrent_account_ref,
                newgreedy,
                checked_at,
                observation_set,
                len(self._specs),
            )
            await self._store_health(
                session,
                "qbittorrent",
                spec.qbittorrent_account_ref,
                qbittorrent,
                checked_at,
                observation_set,
                len(self._specs),
            )
            if inventory is not None:
                snapshot = QBittorrentInventorySnapshot(
                    account_ref=spec.qbittorrent_account_ref,
                    observation_set=observation_set,
                    item_count=len(inventory),
                    truncated=inventory_truncated,
                    checked_at=checked_at,
                )
                session.add(snapshot)
                await session.flush()
                session.add_all(
                    [
                        QBittorrentInventoryItem(
                            snapshot_id=snapshot.id,
                            info_hash=item.info_hash,
                            storage_key=item.storage_key,
                            claims_wos_identity=item.claims_wos_identity,
                        )
                        for item in inventory
                    ]
                )
                await session.execute(
                    delete(QBittorrentInventorySnapshot).where(
                        QBittorrentInventorySnapshot.account_ref == spec.qbittorrent_account_ref,
                        QBittorrentInventorySnapshot.checked_at < checked_at - SNAPSHOT_RETENTION,
                    )
                )

    @staticmethod
    async def _store_health(
        session: AsyncSession,
        service: IntegrationServiceName,
        account_ref: uuid.UUID,
        probe: StoredProbe,
        checked_at: datetime,
        observation_set: uuid.UUID,
        account_count: int,
    ) -> None:
        row = await session.get(
            IntegrationServiceHealth,
            {"service": service, "account_ref": account_ref},
            with_for_update=True,
        )
        if row is None:
            row = IntegrationServiceHealth(service=service, account_ref=account_ref)
            session.add(row)
        row.observation_set = observation_set
        row.account_count = account_count
        row.state, row.latency_ms, row.error_code = probe
        row.checked_at = checked_at
        row.updated_at = checked_at


def _milliseconds(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
