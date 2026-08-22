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
from app.observability import MetricsRegistry

router = APIRouter()


def get_metrics_registry(request: Request) -> MetricsRegistry:
    return cast(MetricsRegistry, request.app.state.metrics_registry)


MetricsRegistryDependency = Annotated[MetricsRegistry, Depends(get_metrics_registry)]


@router.get("", include_in_schema=False)
async def metrics(
    db: DbSession,
    redis: RedisCoordinatorDependency,
    monitor: ExternalServicesMonitorDependency,
    registry: MetricsRegistryDependency,
) -> Response:
    now = datetime.now(UTC)
    db_started = perf_counter()
    job_counts = {
        state: count
        for state, count in (
            await db.execute(select(TorrentJob.state, func.count()).group_by(TorrentJob.state))
        ).all()
    }
    oldest_queued = await db.scalar(
        select(func.min(TorrentJob.created_at)).where(TorrentJob.state == TorrentJobState.QUEUED)
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
            select(func.count()).select_from(DownloadLease).where(DownloadLease.expires_at > now)
        )
        or 0
    )
    db_latency = perf_counter() - db_started

    redis_health = await redis.check_health()
    services = await monitor.snapshot()
    job_durations = [
        max(0.0, (finished.replace(tzinfo=UTC) - created.replace(tzinfo=UTC)).total_seconds())
        for created, finished in finished_rows
        if finished is not None
    ]
    oldest_age = (
        max(0.0, (now - oldest_queued.replace(tzinfo=UTC)).total_seconds())
        if oldest_queued is not None
        else 0.0
    )
    average_job_duration = sum(job_durations) / len(job_durations) if job_durations else 0
    desired_generation = scheduler.desired_generation if scheduler else 0
    applied_generation = scheduler.applied_generation if scheduler else 0
    lines = registry.render_api()
    lines.extend(
        [
            "# HELP wos_jobs Current durable jobs by fixed state.",
            "# TYPE wos_jobs gauge",
        ]
    )
    for state in TorrentJobState:
        lines.append(f'wos_jobs{{state="{state.value.lower()}"}} {job_counts.get(state, 0)}')
    lines.extend(
        [
            "# HELP wos_job_queue_oldest_age_seconds Age of the oldest queued job.",
            "# TYPE wos_job_queue_oldest_age_seconds gauge",
            f"wos_job_queue_oldest_age_seconds {oldest_age:.3f}",
            "# HELP wos_job_retries_total Durable job attempts beyond initial scheduling.",
            "# TYPE wos_job_retries_total gauge",
            f"wos_job_retries_total {retries}",
            "# HELP wos_job_duration_seconds_avg Mean duration of the last 500 finished jobs.",
            "# TYPE wos_job_duration_seconds_avg gauge",
            f"wos_job_duration_seconds_avg {average_job_duration:.3f}",
            "# HELP wos_scheduler_generation Scheduler desired and applied generation.",
            "# TYPE wos_scheduler_generation gauge",
            f'wos_scheduler_generation{{state="desired"}} {desired_generation}',
            f'wos_scheduler_generation{{state="applied"}} {applied_generation}',
            "# HELP wos_download_leases_active Active download leases.",
            "# TYPE wos_download_leases_active gauge",
            f"wos_download_leases_active {active_leases}",
            "# HELP wos_database_query_latency_seconds Metrics database query latency.",
            "# TYPE wos_database_query_latency_seconds gauge",
            f"wos_database_query_latency_seconds {db_latency:.6f}",
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
            f'wos_storage_bytes{{kind="managed"}} {ledger.managed_bytes if ledger else 0}',
            f'wos_storage_bytes{{kind="disk_total"}} {ledger.disk_total_bytes if ledger else 0}',
            f'wos_storage_bytes{{kind="disk_free"}} {ledger.disk_free_bytes if ledger else 0}',
            "# HELP wos_storage_pressure Storage pressure state.",
            "# TYPE wos_storage_pressure gauge",
        ]
    )
    current_pressure = ledger.pressure.value.lower() if ledger is not None else "normal"
    for pressure in ("normal", "warning", "critical"):
        lines.append(
            f'wos_storage_pressure{{state="{pressure}"}} {int(pressure == current_pressure)}'
        )
    return Response(
        "\n".join(lines) + "\n",
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
