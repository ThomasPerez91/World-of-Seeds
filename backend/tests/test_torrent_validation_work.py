import asyncio
import json
import subprocess
import sys
import threading
from functools import partial
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_v2_torrent_api import login, prepare_user, torrent_content

from app.api.routes import torrents_v2
from app.models import ManagedTorrent
from app.torrents import TorrentValidationError
from app.torrents.validation_work import TorrentValidationBusyError, TorrentValidationPool


@pytest.mark.parametrize(("workers", "pending"), [(0, 1), (2, 1), (1, 0)])
def test_invalid_work_budgets_are_rejected(workers: int, pending: int) -> None:
    with pytest.raises(ValueError):
        TorrentValidationPool(workers=workers, max_pending=pending)


async def wait_until_entered(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 2), "worker did not start"


@pytest.mark.asyncio
async def test_running_work_keeps_its_slot_after_cancellation() -> None:
    pool = TorrentValidationPool(workers=1, max_pending=1)
    entered, release = threading.Event(), threading.Event()

    def operation() -> int:
        entered.set()
        assert release.wait(3)
        return 7

    task = asyncio.create_task(pool.run(operation))
    try:
        await wait_until_entered(entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(TorrentValidationBusyError):
            await pool.run(lambda: 8)
        release.set()
        async with asyncio.timeout(2):
            while True:
                try:
                    assert await pool.run(lambda: 9) == 9
                    break
                except TorrentValidationBusyError:
                    await asyncio.sleep(0)
    finally:
        release.set()
        await pool.aclose()


@pytest.mark.asyncio
async def test_cancelled_queue_entries_cannot_grow_the_queue_or_execute() -> None:
    pool = TorrentValidationPool(workers=1, max_pending=2)
    entered, release, queued_ran = threading.Event(), threading.Event(), threading.Event()

    def operation() -> None:
        entered.set()
        assert release.wait(3)

    running = asyncio.create_task(pool.run(operation))
    queued: asyncio.Task[None] | None = None
    try:
        await wait_until_entered(entered)
        queued = asyncio.create_task(pool.run(queued_ran.set))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        for _ in range(50):
            with pytest.raises(TorrentValidationBusyError):
                await pool.run(lambda: None)
        release.set()
        await running
    finally:
        release.set()
        await pool.aclose()
        await asyncio.gather(running, *([queued] if queued else []), return_exceptions=True)
    assert not queued_ran.is_set()


@pytest.mark.asyncio
async def test_parallel_work_is_bounded_and_does_not_block_the_event_loop() -> None:
    pool = TorrentValidationPool(workers=2, max_pending=4)
    release = threading.Event()
    entered = [threading.Event() for _ in range(2)]
    lock = threading.Lock()
    running = peak = 0

    def operation(index: int) -> int:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        try:
            if index < 2:
                entered[index].set()
            assert release.wait(3)
            return index
        finally:
            with lock:
                running -= 1

    tasks = [asyncio.create_task(pool.run(partial(operation, i))) for i in range(4)]
    try:
        for event in entered:
            await wait_until_entered(event)
        await asyncio.sleep(0.01)  # The loop runs while both real worker threads remain blocked.
        assert not any(task.done() for task in tasks)
        with pytest.raises(TorrentValidationBusyError):
            await pool.run(lambda: 5)
        release.set()
        assert await asyncio.gather(*tasks) == [0, 1, 2, 3]
        assert peak == 2
    finally:
        release.set()
        await pool.aclose()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_errors_release_capacity_and_shutdown_requires_explicit_restart() -> None:
    pool = TorrentValidationPool(workers=1, max_pending=1)

    def invalid() -> None:
        raise TorrentValidationError("invalid fixture")

    with pytest.raises(TorrentValidationError):
        await pool.run(invalid)
    assert await pool.run(lambda: 3) == 3
    await pool.aclose()
    with pytest.raises(TorrentValidationBusyError):
        await pool.run(lambda: 4)
    pool.start()
    try:
        assert await pool.run(lambda: 5) == 5
    finally:
        await pool.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("admin", [False, True])
async def test_upload_saturation_is_temporary_and_creates_no_torrent(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, admin: bool
) -> None:
    user = await prepare_user(db_session)
    user.is_admin = admin
    await db_session.commit()
    headers = await login(client)
    pool = TorrentValidationPool(workers=1, max_pending=1)
    monkeypatch.setattr(torrents_v2, "torrent_validation_pool", pool)
    entered, release = threading.Event(), threading.Event()

    def occupy() -> None:
        entered.set()
        assert release.wait(3)

    task = asyncio.create_task(pool.run(occupy))
    try:
        await wait_until_entered(entered)
        response = await client.post(
            "/api/v2/torrents",
            headers=headers,
            files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        )
        assert response.status_code == 503, response.text
        assert response.headers["retry-after"] == "1"
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["detail"]["code"] == "torrent_validation_busy"
        assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0
    finally:
        release.set()
        await task
        await pool.aclose()


def test_large_manifest_validation_finishes_within_a_bounded_time() -> None:
    # A subprocess deadline kills quadratic regressions instead of hanging the test suite.
    script = Path(__file__).resolve().parents[2] / "scripts/benchmark_torrent_validation.py"
    result = subprocess.run(
        [sys.executable, str(script), "--files", "30000"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    measurements = json.loads(result.stdout)
    assert measurements[0]["files"] == 30_000
    assert measurements[0]["infohash_preserved"]
