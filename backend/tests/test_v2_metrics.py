import asyncio
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
from app.observability import (
    MetricsRegistry,
    OperationalMetricsCache,
    OperationalMetricsSnapshot,
)


def test_api_registry_uses_bounded_route_templates_and_status_classes() -> None:
    registry = MetricsRegistry()

    registry.observe_request("GET", "/api/v2/torrents/{torrent_request_id}", 404, 0.25)
    registry.observe_request("UNBOUNDED", "/private/user-name/file.mkv", 500, 0.5)
    output = "\n".join(registry.render_api())

    assert 'method="GET",route="/api/v2/torrents/{torrent_request_id}",status_class="4xx"' in output
    assert 'method="OTHER",route="unmatched",status_class="5xx"' in output
    assert "user-name" not in output


@pytest.mark.asyncio
async def test_operational_metrics_cache_coalesces_concurrent_scrapes() -> None:
    cache = OperationalMetricsCache(ttl_seconds=15)
    calls = 0
    snapshot = OperationalMetricsSnapshot(
        job_counts=(),
        oldest_queued_at=None,
        retries=0,
        average_job_duration_seconds=0,
        desired_generation=0,
        applied_generation=0,
        active_leases=0,
        managed_bytes=0,
        disk_total_bytes=0,
        disk_free_bytes=0,
        storage_pressure="normal",
        database_query_latency_seconds=0,
    )

    async def load() -> OperationalMetricsSnapshot:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return snapshot

    results = await asyncio.gather(*(cache.get(load) for _ in range(25)))

    assert calls == 1
    assert all(result is snapshot for result in results)


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
                attempt_count=2,
                max_attempts=3,
                available_at=now - timedelta(seconds=5),
                created_at=now - timedelta(seconds=5),
            ),
            TorrentJob(
                managed_torrent_id=torrent.id,
                job_type="PURGE_TORRENT",
                idempotency_key="metrics-deferred-purge",
                state=TorrentJobState.QUEUED,
                attempt_count=0,
                max_attempts=20,
                available_at=now + timedelta(hours=48),
                created_at=now - timedelta(days=3),
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
    oldest = next(
        float(line.split()[-1])
        for line in output.splitlines()
        if line.startswith("wos_job_queue_oldest_age_seconds ")
    )
    # The deferred purge created three days ago is not a stuck job: only the claimable one counts.
    assert 0 <= oldest < 120
    assert "private-release-name" not in output
    assert "a" * 40 not in output
    assert "passkey" not in output.lower()


def test_duration_histogram_stays_monotonic_beyond_the_old_sample_window() -> None:
    registry = MetricsRegistry()
    for index in range(12_000):
        registry.observe_request("GET", "/api/v2/torrents", 200, 0.02 if index % 2 else 0.2)

    lines = registry.render_api()
    prefix = 'wos_api_request_duration_seconds_bucket{method="GET",route="/api/v2/torrents",'
    buckets = {
        line.split('le="')[1].split('"')[0]: int(line.rsplit(" ", 1)[1])
        for line in lines
        if line.startswith(prefix)
    }
    count = next(
        int(line.rsplit(" ", 1)[1])
        for line in lines
        if line.startswith("wos_api_request_duration_seconds_count")
    )

    # Nothing is dropped after 10 000 requests: counters only ever grow.
    assert count == 12_000
    assert buckets["+Inf"] == 12_000
    assert buckets["0.01"] == 0
    assert buckets["0.05"] == 6_000
    assert buckets["0.25"] == 12_000
    ordered = [buckets[key] for key in ("0.01", "0.05", "0.1", "0.25", "0.5", "+Inf")]
    assert ordered == sorted(ordered)
