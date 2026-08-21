import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    TrackerActivity,
    TrackerActivityOutcome,
    TrackerActivityType,
    TrackerDiagnosticCode,
)


class TrackerActivityError(ValueError):
    """A tracker activity would violate the secret-safe append-only contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _require_opaque_reference(value: uuid.UUID, field: str) -> None:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        raise TrackerActivityError(f"invalid_{field}")


def _require_aware_timestamp(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TrackerActivityError("invalid_tracker_activity_timestamp")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def assign_managed_torrent_account_refs(
    session: AsyncSession,
    managed_torrent_id: uuid.UUID,
    *,
    tracker_account_ref: uuid.UUID,
    qbittorrent_account_ref: uuid.UUID,
) -> ManagedTorrent:
    """Assign opaque deployment references once; repeated identical assignment is safe."""

    _require_opaque_reference(managed_torrent_id, "managed_torrent_id")
    _require_opaque_reference(tracker_account_ref, "tracker_account_ref")
    _require_opaque_reference(qbittorrent_account_ref, "qbittorrent_account_ref")
    torrent = await session.scalar(
        select(ManagedTorrent).where(ManagedTorrent.id == managed_torrent_id).with_for_update()
    )
    if torrent is None:
        raise TrackerActivityError("managed_torrent_not_found")
    if torrent.tracker_account_ref not in {None, tracker_account_ref}:
        raise TrackerActivityError("tracker_account_reference_conflict")
    if torrent.qbittorrent_account_ref not in {None, qbittorrent_account_ref}:
        raise TrackerActivityError("qbittorrent_account_reference_conflict")
    torrent.tracker_account_ref = tracker_account_ref
    torrent.qbittorrent_account_ref = qbittorrent_account_ref
    await session.flush()
    return torrent


async def record_tracker_activity(
    session: AsyncSession,
    managed_torrent_id: uuid.UUID,
    *,
    event_key: uuid.UUID,
    tracker_account_ref: uuid.UUID,
    event_type: TrackerActivityType,
    outcome: TrackerActivityOutcome,
    diagnostic_code: TrackerDiagnosticCode | None,
    occurred_at: datetime,
) -> TrackerActivity:
    """Append or replay one bounded event without accepting free-form diagnostic text."""

    _require_opaque_reference(managed_torrent_id, "managed_torrent_id")
    _require_opaque_reference(event_key, "tracker_event_key")
    _require_opaque_reference(tracker_account_ref, "tracker_account_ref")
    _require_aware_timestamp(occurred_at)
    if not isinstance(event_type, TrackerActivityType):
        raise TrackerActivityError("invalid_tracker_activity_type")
    if not isinstance(outcome, TrackerActivityOutcome):
        raise TrackerActivityError("invalid_tracker_activity_outcome")
    if diagnostic_code is not None and not isinstance(diagnostic_code, TrackerDiagnosticCode):
        raise TrackerActivityError("invalid_tracker_diagnostic_code")
    if (outcome is TrackerActivityOutcome.SUCCESS) != (diagnostic_code is None):
        raise TrackerActivityError("inconsistent_tracker_diagnostic")

    existing = await session.scalar(
        select(TrackerActivity).where(TrackerActivity.event_key == event_key)
    )
    if existing is not None:
        if (
            existing.managed_torrent_id != managed_torrent_id
            or existing.tracker_account_ref != tracker_account_ref
            or existing.event_type is not event_type
            or existing.outcome is not outcome
            or existing.diagnostic_code is not diagnostic_code
            or _as_utc(existing.occurred_at) != _as_utc(occurred_at)
        ):
            raise TrackerActivityError("tracker_event_key_conflict")
        return existing

    torrent = await session.scalar(
        select(ManagedTorrent).where(ManagedTorrent.id == managed_torrent_id).with_for_update()
    )
    if torrent is None:
        raise TrackerActivityError("managed_torrent_not_found")
    if torrent.tracker_account_ref != tracker_account_ref:
        raise TrackerActivityError("tracker_account_reference_mismatch")

    activity = TrackerActivity(
        event_key=event_key,
        managed_torrent=torrent,
        tracker_account_ref=tracker_account_ref,
        event_type=event_type,
        outcome=outcome,
        diagnostic_code=diagnostic_code,
        occurred_at=occurred_at,
    )
    session.add(activity)
    await session.flush()
    return activity
