import asyncio
import threading

import pytest

from app.admin.disk_reads import ReconciliationBusyError, ReconciliationDiskReads


@pytest.mark.asyncio
async def test_burst_never_queues_or_starts_another_disk_operation() -> None:
    reads = ReconciliationDiskReads()
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    calls = 0

    def blocked() -> int:
        nonlocal calls
        calls += 1
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        return 7

    first = asyncio.create_task(reads.run(blocked))
    try:
        await entered.wait()
        results = await asyncio.gather(
            *(reads.run(blocked) for _ in range(20)), return_exceptions=True
        )
        assert all(isinstance(result, ReconciliationBusyError) for result in results)
        assert calls == 1
        assert not await reads.wait_idle(0.01)
    finally:
        release.set()
        assert await first == 7
    assert await reads.wait_idle()
    assert await reads.run(lambda: 8) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_cancelled_caller_keeps_admission_until_real_disk_operation_finishes(
    failure: bool,
) -> None:
    reads = ReconciliationDiskReads()
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def blocked() -> int:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)
        if failure:
            raise RuntimeError("disk failed after caller cancellation")
        return 1

    first = asyncio.create_task(reads.run(blocked))
    try:
        await entered.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(ReconciliationBusyError):
            await reads.run(lambda: pytest.fail("Cancellation must not open another disk slot"))
        assert not await reads.wait_idle(0.01)
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
    assert await reads.wait_idle()
    assert await reads.run(lambda: 2) == 2


@pytest.mark.asyncio
async def test_disk_error_propagates_and_releases_admission() -> None:
    reads = ReconciliationDiskReads()

    def failure() -> None:
        raise OSError("disk failed")

    with pytest.raises(OSError):
        await reads.run(failure)
    assert await reads.wait_idle()
    assert await reads.run(lambda: 2) == 2


@pytest.mark.asyncio
async def test_admission_is_per_application_not_global() -> None:
    first = ReconciliationDiskReads()
    second = ReconciliationDiskReads()
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def blocked() -> None:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(3)

    task = asyncio.create_task(first.run(blocked))
    try:
        await entered.wait()
        assert await second.run(lambda: 3) == 3
    finally:
        release.set()
        await task
