import asyncio
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select

from app.auth.dependencies import DbSession
from app.coordination.dependencies import RedisCoordinatorDependency
from app.integrations.dependencies import ExternalServicesMonitorDependency
from app.models import (
    DownloadLease,
    SchedulerState,
    StorageLedger,
    TorrentJob,
    TorrentJobState,
)
from app.observability import (
    MetricsRegistry,
    OperationalMetricsCache,
    OperationalMetricsSnapshot,
)

router = APIRouter()


def get_metrics_registry(request: Request) -> MetricsRegistry:
    return cast(MetricsRegistry, request.app.state.metrics_registry)


MetricsRegistryDependency = Annotated[MetricsRegistry, Depends(get_metrics_registry)]


def get_operational_metrics_cache(request: Request) -> OperationalMetricsCache:
    return cast(OperationalMetricsCache, request.app.state.operational_metrics_cache)


OperationalMetricsCacheDependency = Annotated[
    OperationalMetricsCache,
    Depends(get_operational_metrics_cache),
]


async def _collect_operational_metrics(
    db: DbSession,
    *,
    now: datetime,
) -> OperationalMetricsSnapshot:
    db_started = perf_counter()
    try:
        job_counts = tuple(
            (state.value, int(count))
            for state, count in (
                await db.execute(select(TorrentJob.state, func.count()).group_by(TorrentJob.state))
            ).all()
        )
        oldest_queued = await db.scalar(
            select(func.min(TorrentJob.created_at)).where(
                TorrentJob.state == TorrentJobState.QUEUED
            )
        )
        retries = int(
            await db.scalar(select(func.coalesce(func.sum(TorrentJob.attempt_count), 0))) or 0
        )
        finished_rows = (
            await db.execute(
                select(TorrentJob.created_at, TorrentJob.finished_at)
                .where(TorrentJob.finished_at.is_not(None))
                .order_by(TorrentJob.finished_at.desc())
                .limit(500)
            )
        ).all()
        scheduler = await db.get(SchedulerState, 1)
        ledger = await db.get(StorageLedger, 1)
        active_leases = int(
            await db.scalar(
                select(func.count())
                .select_from(DownloadLease)
                .where(DownloadLease.expires_at > now)
            )
            or 0
        )
        job_durations = [
            max(0.0, (finished.replace(tzinfo=UTC) - created.replace(tzinfo=UTC)).total_seconds())
            for created, finished in finished_rows
            if finished is not None
        ]
        return OperationalMetricsSnapshot(
            job_counts=job_counts,
            oldest_queued_at=oldest_queued,
            retries=retries,
            average_job_duration_seconds=(
                sum(job_durations) / len(job_durations) if job_durations else 0.0
            ),
            desired_generation=scheduler.desired_generation if scheduler else 0,
            applied_generation=scheduler.applied_generation if scheduler else 0,
            active_leases=active_leases,
            managed_bytes=ledger.managed_bytes if ledger else 0,
            disk_total_bytes=ledger.disk_total_bytes if ledger else 0,
            disk_free_bytes=ledger.disk_free_bytes if ledger else 0,
            storage_pressure=ledger.pressure.value.lower() if ledger else "normal",
            database_query_latency_seconds=perf_counter() - db_started,
        )
    finally:
        # A scrape can now probe Redis/qB/NewGreedy without retaining a SQL transaction.
        await db.rollback()


@router.get("", include_in_schema=False)
async def metrics(
    db: DbSession,
    redis: RedisCoordinatorDependency,
    monitor: ExternalServicesMonitorDependency,
    registry: MetricsRegistryDependency,
    operational_cache: OperationalMetricsCacheDependency,
) -> Response:
    now = datetime.now(UTC)
    operational = await operational_cache.get(lambda: _collect_operational_metrics(db, now=now))
    redis_health, services = await asyncio.gather(
        redis.check_health(),
        monitor.snapshot(),
    )
    job_counts = dict(operational.job_counts)
    oldest_age = (
        max(0.0, (now - operational.oldest_queued_at.replace(tzinfo=UTC)).total_seconds())
        if operational.oldest_queued_at is not None
        else 0.0
    )
    lines = registry.render_api()
    lines.extend(
        [
            "# HELP wos_jobs Current durable jobs by fixed state.",
            "# TYPE wos_jobs gauge",
        ]
    )
    for state in TorrentJobState:
        lines.append(f'wos_jobs{{state="{state.value.lower()}"}} {job_counts.get(state.value, 0)}')
    lines.extend(
        [
            "# HELP wos_job_queue_oldest_age_seconds Age of the oldest queued job.",
            "# TYPE wos_job_queue_oldest_age_seconds gauge",
            f"wos_job_queue_oldest_age_seconds {oldest_age:.3f}",
            "# HELP wos_job_retries_total Durable job attempts beyond initial scheduling.",
            "# TYPE wos_job_retries_total gauge",
            f"wos_job_retries_total {operational.retries}",
            "# HELP wos_job_duration_seconds_avg Mean duration of the last 500 finished jobs.",
            "# TYPE wos_job_duration_seconds_avg gauge",
            f"wos_job_duration_seconds_avg {operational.average_job_duration_seconds:.3f}",
            "# HELP wos_scheduler_generation Scheduler desired and applied generation.",
            "# TYPE wos_scheduler_generation gauge",
            f'wos_scheduler_generation{{state="desired"}} {operational.desired_generation}',
            f'wos_scheduler_generation{{state="applied"}} {operational.applied_generation}',
            "# HELP wos_download_leases_active Active download leases.",
            "# TYPE wos_download_leases_active gauge",
            f"wos_download_leases_active {operational.active_leases}",
            "# HELP wos_database_query_latency_seconds Last metrics snapshot query latency.",
            "# TYPE wos_database_query_latency_seconds gauge",
            f"wos_database_query_latency_seconds {operational.database_query_latency_seconds:.6f}",
            "# HELP wos_redis_up Redis health without business identifiers.",
            "# TYPE wos_redis_up gauge",
            f"wos_redis_up {1 if redis_health.state == 'healthy' else 0}",
            "# HELP wos_qbittorrent_up qBittorrent health.",
            "# TYPE wos_qbittorrent_up gauge",
            f"wos_qbittorrent_up {1 if services.qbittorrent.state == 'healthy' else 0}",
            "# HELP wos_qbittorrent_latency_seconds qBittorrent probe latency.",
            "# TYPE wos_qbittorrent_latency_seconds gauge",
            f"wos_qbittorrent_latency_seconds {(services.qbittorrent.latency_ms or 0) / 1000:.3f}",
            "# HELP wos_storage_bytes Shared storage accounting by fixed kind.",
            "# TYPE wos_storage_bytes gauge",
            f'wos_storage_bytes{{kind="managed"}} {operational.managed_bytes}',
            f'wos_storage_bytes{{kind="disk_total"}} {operational.disk_total_bytes}',
            f'wos_storage_bytes{{kind="disk_free"}} {operational.disk_free_bytes}',
            "# HELP wos_storage_pressure Storage pressure state.",
            "# TYPE wos_storage_pressure gauge",
        ]
    )
    for pressure in ("normal", "warning", "critical"):
        lines.append(
            f'wos_storage_pressure{{state="{pressure}"}} '
            f"{int(pressure == operational.storage_pressure)}"
        )
    return Response(
        "\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
