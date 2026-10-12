import asyncio
import threading
import uuid

import pytest

from app.torrents.retry_reads import TorrentRetryBusyError, TorrentRetryReads


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_cancelled_retry_keeps_its_key_until_the_real_read_finishes(fail: bool) -> None:
    reads = TorrentRetryReads()
    key = uuid.uuid4()
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def operation() -> int:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        if fail:
            raise RuntimeError("late failure")
        return 1

    task = asyncio.create_task(reads.run(key, operation))
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(TorrentRetryBusyError):
            await reads.run(key, lambda: 2)
        assert not await reads.wait_idle(0.01)
        # Separate payloads and separate applications have independent admission.
        assert await reads.run(uuid.uuid4(), lambda: 3) == 3
        assert await TorrentRetryReads().run(key, lambda: 4) == 4
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await reads.wait_idle()
    assert await reads.run(key, lambda: 5) == 5


@pytest.mark.asyncio
async def test_failed_retry_read_releases_the_key_and_propagates_the_error() -> None:
    reads = TorrentRetryReads()
    key = uuid.uuid4()

    def fail() -> int:
        raise RuntimeError("disk failure")

    with pytest.raises(RuntimeError, match="disk failure"):
        await reads.run(key, fail)
    assert await reads.run(key, lambda: 1) == 1
    assert await reads.wait_idle()
