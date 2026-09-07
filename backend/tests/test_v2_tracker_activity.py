import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    TrackerActivity,
    TrackerActivityOutcome,
    TrackerActivityType,
    TrackerDiagnosticCode,
)
from app.torrents import (
    TrackerActivityError,
    assign_managed_torrent_account_refs,
    record_tracker_activity,
)

NOW = datetime(2026, 8, 21, 18, tzinfo=UTC)
TRACKER_ACCOUNT_REF = uuid.UUID("11111111-1111-1111-1111-111111111111")
QBITTORRENT_ACCOUNT_REF = uuid.UUID("22222222-2222-2222-2222-222222222222")
EVENT_KEY = uuid.UUID("33333333-3333-3333-3333-333333333333")


async def create_managed_torrent(session: AsyncSession) -> ManagedTorrent:
    torrent = ManagedTorrent(info_hash="a" * 40, name="Example", total_size=10)
    session.add(torrent)
    await session.flush()
    return torrent


@pytest.mark.asyncio
async def test_opaque_account_assignment_and_activity_are_persisted(
    db_session: AsyncSession,
) -> None:
    torrent = await create_managed_torrent(db_session)
    assigned = await assign_managed_torrent_account_refs(
        db_session,
        torrent.id,
        tracker_account_ref=TRACKER_ACCOUNT_REF,
        qbittorrent_account_ref=QBITTORRENT_ACCOUNT_REF,
    )
    activity = await record_tracker_activity(
        db_session,
        torrent.id,
        event_key=EVENT_KEY,
        tracker_account_ref=TRACKER_ACCOUNT_REF,
        event_type=TrackerActivityType.ANNOUNCE,
        outcome=TrackerActivityOutcome.SUCCESS,
        diagnostic_code=None,
        occurred_at=NOW,
    )
    await db_session.commit()

    assert assigned.tracker_account_ref == TRACKER_ACCOUNT_REF
    assert assigned.qbittorrent_account_ref == QBITTORRENT_ACCOUNT_REF
    assert activity.managed_torrent_id == torrent.id
    assert activity.tracker_account_ref == TRACKER_ACCOUNT_REF
    assert activity.diagnostic_code is None


@pytest.mark.asyncio
async def test_activity_replay_is_idempotent_but_event_key_collision_is_rejected(
    db_session: AsyncSession,
) -> None:
    torrent = await create_managed_torrent(db_session)
    await assign_managed_torrent_account_refs(
        db_session,
        torrent.id,
        tracker_account_ref=TRACKER_ACCOUNT_REF,
        qbittorrent_account_ref=QBITTORRENT_ACCOUNT_REF,
    )
    first = await record_tracker_activity(
        db_session,
        torrent.id,
        event_key=EVENT_KEY,
        tracker_account_ref=TRACKER_ACCOUNT_REF,
        event_type=TrackerActivityType.TRACKER_STATUS,
        outcome=TrackerActivityOutcome.FAILED,
        diagnostic_code=TrackerDiagnosticCode.TRACKER_REJECTED,
        occurred_at=NOW,
    )
    await db_session.commit()
    replay = await record_tracker_activity(
        db_session,
        torrent.id,
        event_key=EVENT_KEY,
        tracker_account_ref=TRACKER_ACCOUNT_REF,
        event_type=TrackerActivityType.TRACKER_STATUS,
        outcome=TrackerActivityOutcome.FAILED,
        diagnostic_code=TrackerDiagnosticCode.TRACKER_REJECTED,
        occurred_at=NOW,
    )

    assert replay.id == first.id
    assert await db_session.scalar(select(func.count()).select_from(TrackerActivity)) == 1

    with pytest.raises(TrackerActivityError) as caught:
        await record_tracker_activity(
            db_session,
            torrent.id,
            event_key=EVENT_KEY,
            tracker_account_ref=TRACKER_ACCOUNT_REF,
            event_type=TrackerActivityType.SCRAPE,
            outcome=TrackerActivityOutcome.FAILED,
            diagnostic_code=TrackerDiagnosticCode.TRACKER_REJECTED,
            occurred_at=NOW,
        )
    assert caught.value.code == "tracker_event_key_conflict"


@pytest.mark.asyncio
async def test_account_references_are_idempotent_and_cannot_be_silently_reassigned(
    db_session: AsyncSession,
) -> None:
    torrent = await create_managed_torrent(db_session)
    await assign_managed_torrent_account_refs(
        db_session,
        torrent.id,
        tracker_account_ref=TRACKER_ACCOUNT_REF,
        qbittorrent_account_ref=QBITTORRENT_ACCOUNT_REF,
    )
    same = await assign_managed_torrent_account_refs(
        db_session,
        torrent.id,
        tracker_account_ref=TRACKER_ACCOUNT_REF,
        qbittorrent_account_ref=QBITTORRENT_ACCOUNT_REF,
    )
    assert same.id == torrent.id

    with pytest.raises(TrackerActivityError) as caught:
        await assign_managed_torrent_account_refs(
            db_session,
            torrent.id,
            tracker_account_ref=uuid.uuid4(),
            qbittorrent_account_ref=QBITTORRENT_ACCOUNT_REF,
        )
    assert caught.value.code == "tracker_account_reference_conflict"


@pytest.mark.asyncio
async def test_activity_requires_the_assigned_opaque_tracker_reference(
    db_session: AsyncSession,
) -> None:
    torrent = await create_managed_torrent(db_session)
    await assign_managed_torrent_account_refs(
        db_session,
        torrent.id,
        tracker_account_ref=TRACKER_ACCOUNT_REF,
        qbittorrent_account_ref=QBITTORRENT_ACCOUNT_REF,
    )

    with pytest.raises(TrackerActivityError) as caught:
        await record_tracker_activity(
            db_session,
            torrent.id,
            event_key=EVENT_KEY,
            tracker_account_ref=uuid.uuid4(),
            event_type=TrackerActivityType.ANNOUNCE,
            outcome=TrackerActivityOutcome.SUCCESS,
            diagnostic_code=None,
            occurred_at=NOW,
        )
    assert caught.value.code == "tracker_account_reference_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "diagnostic_code"),
    [
        (TrackerActivityOutcome.SUCCESS, TrackerDiagnosticCode.UNKNOWN_ERROR),
        (TrackerActivityOutcome.DEGRADED, None),
        (TrackerActivityOutcome.FAILED, None),
    ],
)
async def test_service_rejects_inconsistent_bounded_diagnostics(
    db_session: AsyncSession,
    outcome: TrackerActivityOutcome,
    diagnostic_code: TrackerDiagnosticCode | None,
) -> None:
    torrent = await create_managed_torrent(db_session)
    await assign_managed_torrent_account_refs(
        db_session,
        torrent.id,
        tracker_account_ref=TRACKER_ACCOUNT_REF,
        qbittorrent_account_ref=QBITTORRENT_ACCOUNT_REF,
    )

    with pytest.raises(TrackerActivityError) as caught:
        await record_tracker_activity(
            db_session,
            torrent.id,
            event_key=EVENT_KEY,
            tracker_account_ref=TRACKER_ACCOUNT_REF,
            event_type=TrackerActivityType.ANNOUNCE,
            outcome=outcome,
            diagnostic_code=diagnostic_code,
            occurred_at=NOW,
        )
    assert caught.value.code == "inconsistent_tracker_diagnostic"


@pytest.mark.asyncio
async def test_database_rejects_free_form_or_inconsistent_diagnostics(
    db_session: AsyncSession,
) -> None:
    torrent = await create_managed_torrent(db_session)
    db_session.add(
        TrackerActivity(
            event_key=EVENT_KEY,
            managed_torrent=torrent,
            tracker_account_ref=TRACKER_ACCOUNT_REF,
            event_type="https://c411.org/announce/test-passkey-123",
            outcome=TrackerActivityOutcome.FAILED,
            diagnostic_code=TrackerDiagnosticCode.UNKNOWN_ERROR,
            occurred_at=NOW,
        )
    )
    with pytest.raises(StatementError):
        await db_session.commit()
    await db_session.rollback()

    torrent = await create_managed_torrent(db_session)
    db_session.add(
        TrackerActivity(
            event_key=uuid.uuid4(),
            managed_torrent=torrent,
            tracker_account_ref=TRACKER_ACCOUNT_REF,
            event_type=TrackerActivityType.ANNOUNCE,
            outcome=TrackerActivityOutcome.SUCCESS,
            diagnostic_code=TrackerDiagnosticCode.UNKNOWN_ERROR,
            occurred_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


def test_tracker_activity_has_no_free_form_secret_bearing_columns() -> None:
    assert set(TrackerActivity.__table__.columns.keys()) == {
        "id",
        "event_key",
        "managed_torrent_id",
        "tracker_account_ref",
        "event_type",
        "outcome",
        "diagnostic_code",
        "occurred_at",
        "created_at",
    }
    assert {state.value for state in TrackerActivityOutcome} == {
        "SUCCESS",
        "DEGRADED",
        "FAILED",
    }
