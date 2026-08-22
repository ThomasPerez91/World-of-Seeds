from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    SchedulerState,
    StorageLedger,
    TorrentJob,
    TorrentJobState,
)
from app.observability import MetricsRegistry


def test_api_registry_uses_bounded_route_templates_and_status_classes() -> None:
    registry = MetricsRegistry()

    registry.observe_request("GET", "/api/v2/torrents/{torrent_request_id}", 404, 0.25)
    registry.observe_request("UNBOUNDED", "/private/user-name/file.mkv", 500, 0.5)
    output = "\n".join(registry.render_api())

    assert 'method="GET",route="/api/v2/torrents/{torrent_request_id}",status_class="4xx"' in output
    assert 'method="OTHER",route="unmatched",status_class="5xx"' in output
    assert "user-name" not in output


@pytest.mark.asyncio
async def test_metrics_expose_fixed_operational_dimensions_without_business_identifiers(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    torrent = ManagedTorrent(
        info_hash="a" * 40,
        name="private-release-name.mkv",
        total_size=100,
    )
    db_session.add(torrent)
    await db_session.flush()
    db_session.add_all(
        [
            TorrentJob(
                managed_torrent_id=torrent.id,
                job_type="ADD_TORRENT",
                idempotency_key="metrics-job",
                state=TorrentJobState.QUEUED,
                attempt_count=1,
                max_attempts=3,
                available_at=now,
                created_at=now - timedelta(seconds=5),
            ),
            SchedulerState(id=1, desired_generation=3, applied_generation=2, rounds=4),
            StorageLedger(
                id=1,
                managed_bytes=100,
                disk_total_bytes=1000,
                disk_free_bytes=600,
            ),
        ]
    )
    await db_session.commit()

    await client.get("/api/v1/health/live")
    response = await client.get("/api/v2/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    output = response.text
    for metric in (
        "wos_api_requests_total",
        "wos_jobs",
        "wos_job_queue_oldest_age_seconds",
        "wos_job_retries_total 1",
        'wos_scheduler_generation{state="desired"} 3',
        "wos_download_leases_active 0",
        "wos_database_query_latency_seconds",
        "wos_redis_up",
        "wos_qbittorrent_up",
        'wos_storage_bytes{kind="managed"} 100',
        "wos_storage_pressure",
    ):
        assert metric in output
    assert "private-release-name" not in output
    assert "a" * 40 not in output
    assert "passkey" not in output.lower()
