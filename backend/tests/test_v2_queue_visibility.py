from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import (
    Base,
    ManagedTorrent,
    ManagedTorrentState,
    SchedulerState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.scheduler.queue_visibility import load_torrent_queue_visibility

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


@pytest_asyncio.fixture
async def database(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'queue-visibility.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine, async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _queued(
    session: AsyncSession,
    *,
    index: int,
    owners: int = 1,
    state: ManagedTorrentState = ManagedTorrentState.PAUSED,
    desired_active: bool = False,
    desired_priority: int | None = None,
    retry_at: datetime | None = None,
    qb_state: str | None = None,
) -> ManagedTorrent:
    torrent = ManagedTorrent(
        id=uuid.UUID(int=10_000 + index),
        storage_key=uuid.UUID(int=20_000 + index),
        info_hash=f"{30_000 + index:040x}",
        name=f"queue-{index}",
        total_size=(index + 1) * 1024,
        state=state,
        desired_active=desired_active,
        desired_priority=desired_priority,
        scheduler_retry_at=retry_at,
        qb_state=qb_state,
        created_at=NOW + timedelta(microseconds=index),
        updated_at=NOW,
    )
    session.add(torrent)
    for owner_index in range(owners):
        user = User(
            id=uuid.UUID(int=40_000 + index * 10 + owner_index),
            username=f"queue-{index}-owner-{owner_index}",
            password_hash="test-password-hash",
        )
        session.add_all(
            [
                user,
                TorrentRequest(
                    id=uuid.UUID(int=50_000 + index * 10 + owner_index),
                    user=user,
                    managed_torrent=torrent,
                    state=TorrentRequestState.ACTIVE,
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ]
        )
    return torrent


@pytest.mark.asyncio
async def test_estimate_uses_durable_circular_scan_order_and_is_read_only(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    engine, sessions = database
    async with sessions() as session, session.begin():
        first = await _queued(session, index=1)
        second = await _queued(session, index=2, owners=2)
        third = await _queued(session, index=3)
        session.add(
            SchedulerState(
                id=1,
                desired_generation=7,
                applied_generation=7,
                rounds=11,
                scan_cursor_created_at=first.created_at,
                scan_cursor_id=first.id,
                updated_at=NOW,
            )
        )

    statements = 0

    def count_statement(*_: object) -> None:
        nonlocal statements
        statements += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        async with sessions() as session:
            torrents = tuple(
                (
                    await session.scalars(
                        select(ManagedTorrent).order_by(ManagedTorrent.created_at)
                    )
                ).all()
            )
            statements = 0
            result = await load_torrent_queue_visibility(session, torrents, now=NOW)
            visibility_queries = statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)

    assert result[second.id].position_estimate == 1
    assert result[third.id].position_estimate == 2
    assert result[first.id].position_estimate == 3
    assert {item.total_estimate for item in result.values()} == {3}
    assert visibility_queries == 2
    async with sessions() as session:
        scheduler = await session.get(SchedulerState, 1)
        torrents = tuple((await session.scalars(select(ManagedTorrent))).all())
    assert scheduler is not None
    assert scheduler.desired_generation == scheduler.applied_generation == 7
    assert scheduler.rounds == 11
    assert all(not torrent.desired_active for torrent in torrents)


@pytest.mark.asyncio
async def test_backlog_over_one_thousand_keeps_a_page_scoped_numeric_estimate(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, sessions = database
    async with sessions() as session, session.begin():
        target = None
        for index in range(1_005):
            target = await _queued(session, index=index)
    assert target is not None

    async with sessions() as session:
        stored = await session.get(ManagedTorrent, target.id)
        assert stored is not None
        result = await load_torrent_queue_visibility(session, (stored,), now=NOW)

    assert result[target.id].status == "waiting"
    assert result[target.id].position_estimate == 1_005
    assert result[target.id].total_estimate == 1_005


@pytest.mark.asyncio
async def test_selected_stalled_cooldown_and_terminal_torrents_never_get_a_false_rank(
    database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, sessions = database
    async with sessions() as session, session.begin():
        downloading = await _queued(
            session,
            index=1,
            state=ManagedTorrentState.DOWNLOADING,
            desired_active=True,
            desired_priority=0,
        )
        stalled = await _queued(session, index=2, qb_state="stalledDL")
        cooldown = await _queued(
            session,
            index=3,
            retry_at=NOW + timedelta(minutes=3),
        )
        ready = await _queued(session, index=4, state=ManagedTorrentState.READY)

    async with sessions() as session:
        stored = tuple((await session.scalars(select(ManagedTorrent))).all())
        result = await load_torrent_queue_visibility(session, stored, now=NOW)

    assert result[downloading.id].status == "downloading"
    assert result[stalled.id].status == "stalled"
    assert result[cooldown.id].status == "cooldown"
    assert all(
        result[item.id].position_estimate is None for item in (downloading, stalled, cooldown)
    )
    assert ready.id not in result
