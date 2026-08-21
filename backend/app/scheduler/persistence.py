from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchedulerDeficit, SchedulerState
from app.scheduler.weighted_fair import SchedulerLedger


class SchedulerLeaseLostError(RuntimeError):
    """The process no longer owns the durable scheduler lease."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def acquire_scheduler_lease(
    session: AsyncSession,
    *,
    owner: str,
    now: datetime,
    ttl: timedelta,
) -> SchedulerState | None:
    if not 1 <= len(owner) <= 128:
        raise ValueError("scheduler lease owner must contain between 1 and 128 characters")
    if ttl <= timedelta(0):
        raise ValueError("scheduler lease TTL must be positive")
    await _ensure_scheduler_state(session, now=now)
    state = await session.scalar(
        select(SchedulerState).where(SchedulerState.id == 1).with_for_update()
    )
    if state is None:
        raise RuntimeError("scheduler singleton row could not be initialized")
    if (
        state.lease_owner not in {None, owner}
        and state.lease_expires_at is not None
        and _utc(state.lease_expires_at) > _utc(now)
    ):
        return None
    state.lease_owner = owner
    state.lease_expires_at = now + ttl
    state.updated_at = now
    await session.flush()
    return state


async def load_scheduler_ledger(
    session: AsyncSession,
    state: SchedulerState,
) -> SchedulerLedger:
    rows = (
        await session.scalars(select(SchedulerDeficit).order_by(SchedulerDeficit.user_id))
    ).all()
    return SchedulerLedger(
        deficits={row.user_id: row.credit for row in rows},
        rounds=state.rounds,
        cursor_user_id=state.cursor_user_id,
    )


async def persist_scheduler_ledger(
    session: AsyncSession,
    state: SchedulerState,
    ledger: SchedulerLedger,
    *,
    now: datetime,
) -> None:
    await session.execute(delete(SchedulerDeficit))
    session.add_all(
        SchedulerDeficit(user_id=user_id, credit=credit, updated_at=now)
        for user_id, credit in ledger.deficits.items()
    )
    state.rounds = ledger.rounds
    state.cursor_user_id = ledger.cursor_user_id
    state.updated_at = now
    await session.flush()


async def mark_scheduler_generation_applied(
    session: AsyncSession,
    *,
    owner: str,
    generation: int,
    now: datetime,
) -> None:
    state = await session.scalar(
        select(SchedulerState).where(SchedulerState.id == 1).with_for_update()
    )
    if (
        state is None
        or state.lease_owner != owner
        or state.lease_expires_at is None
        or _utc(state.lease_expires_at) <= _utc(now)
        or state.desired_generation != generation
    ):
        raise SchedulerLeaseLostError("scheduler lease or desired generation changed")
    state.applied_generation = generation
    state.updated_at = now
    await session.flush()


async def _ensure_scheduler_state(session: AsyncSession, *, now: datetime) -> None:
    values = {
        "id": 1,
        "desired_generation": 0,
        "applied_generation": 0,
        "rounds": 0,
        "updated_at": now,
    }
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        await session.execute(
            postgresql_insert(SchedulerState).values(**values).on_conflict_do_nothing()
        )
    elif dialect == "sqlite":
        await session.execute(
            sqlite_insert(SchedulerState).values(**values).on_conflict_do_nothing()
        )
    else:
        raise RuntimeError(f"Unsupported scheduler database dialect: {dialect}")
    await session.flush()
