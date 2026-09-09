import asyncio
from typing import cast

import pytest
from starlette.types import Message, Receive, Scope, Send

from app.core.archive_queue import ArchiveDownloadQueueMiddleware


@pytest.mark.asyncio
async def test_archive_requests_wait_for_the_previous_stream() -> None:
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal calls
        del scope, receive
        calls += 1
        current = calls
        if current == 1:
            first_started.set()
        else:
            second_started.set()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        if current == 1:
            await release_first.wait()
        await send({"type": "http.response.body", "body": b"zip", "more_body": False})

    middleware = ArchiveDownloadQueueMiddleware(downstream)
    scope = cast(
        Scope,
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v2/torrents/00000000-0000-4000-8000-000000000001/download-archive",
        },
    )

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(_: Message) -> None:
        return None

    first = asyncio.create_task(middleware(scope, receive, send))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    second = asyncio.create_task(middleware(scope, receive, send))
    await asyncio.sleep(0)

    assert second_started.is_set() is False
    assert calls == 1

    release_first.set()
    await asyncio.wait_for(second_started.wait(), timeout=1)
    await asyncio.gather(first, second)
    assert calls == 2
