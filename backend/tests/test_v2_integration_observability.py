import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.observability_v2 import load_v2_external_services_snapshot
from app.models import IntegrationServiceHealth, IntegrationServiceState

NOW = datetime(2026, 8, 29, 20, tzinfo=UTC)


@pytest.mark.asyncio
async def test_v2_health_aggregates_every_account_and_fails_closed_when_stale(
    db_session: AsyncSession,
) -> None:
    first = uuid.uuid4()
    second = uuid.uuid4()
    observation_set = uuid.uuid4()
    db_session.add_all(
        [
            IntegrationServiceHealth(
                service=service,
                account_ref=account,
                observation_set=observation_set,
                account_count=2,
                state=IntegrationServiceState.HEALTHY,
                latency_ms=latency,
                error_code=None,
                checked_at=NOW,
                updated_at=NOW,
            )
            for service in ("newgreedy", "qbittorrent")
            for account, latency in ((first, 2), (second, 7))
        ]
    )
    await db_session.commit()

    healthy = await load_v2_external_services_snapshot(db_session, now=NOW)
    stale = await load_v2_external_services_snapshot(
        db_session,
        now=NOW + timedelta(minutes=2),
    )

    assert healthy.healthy is True
    assert healthy.newgreedy.latency_ms == 7
    assert healthy.qbittorrent.latency_ms == 7
    assert stale.healthy is False
    assert stale.newgreedy.error_code == "health_stale"
    assert stale.qbittorrent.error_code == "health_stale"


@pytest.mark.asyncio
async def test_v2_health_rejects_an_incomplete_account_registry(
    db_session: AsyncSession,
) -> None:
    observation_set = uuid.uuid4()
    db_session.add_all(
        [
            IntegrationServiceHealth(
                service="newgreedy",
                account_ref=uuid.uuid4(),
                observation_set=observation_set,
                account_count=1,
                state=IntegrationServiceState.HEALTHY,
                latency_ms=1,
                error_code=None,
                checked_at=NOW,
                updated_at=NOW,
            ),
            IntegrationServiceHealth(
                service="qbittorrent",
                account_ref=uuid.uuid4(),
                observation_set=observation_set,
                account_count=1,
                state=IntegrationServiceState.HEALTHY,
                latency_ms=1,
                error_code=None,
                checked_at=NOW,
                updated_at=NOW,
            ),
        ]
    )
    await db_session.commit()

    snapshot = await load_v2_external_services_snapshot(db_session, now=NOW)

    assert snapshot.healthy is False
    assert snapshot.newgreedy.error_code == "health_missing"
    assert snapshot.qbittorrent.error_code == "health_missing"


@pytest.mark.asyncio
async def test_v2_health_rejects_a_partially_published_observation_set(
    db_session: AsyncSession,
) -> None:
    account = uuid.uuid4()
    db_session.add_all(
        [
            IntegrationServiceHealth(
                service=service,
                account_ref=account,
                observation_set=uuid.uuid4(),
                account_count=1,
                state=IntegrationServiceState.HEALTHY,
                latency_ms=1,
                error_code=None,
                checked_at=NOW,
                updated_at=NOW,
            )
            for service in ("newgreedy", "qbittorrent")
        ]
    )
    await db_session.commit()

    snapshot = await load_v2_external_services_snapshot(db_session, now=NOW)

    assert snapshot.healthy is False
    assert snapshot.newgreedy.error_code == "health_missing"
    assert snapshot.qbittorrent.error_code == "health_missing"
