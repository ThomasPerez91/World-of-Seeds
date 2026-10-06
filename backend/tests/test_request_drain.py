import asyncio
import signal
from typing import cast

import httpx
import pytest
import uvicorn
from starlette.types import Message, Scope

from app.core.drain import RequestDrain, RequestDrainMiddleware
from app.server import REQUEST_GRACE_SECONDS, DrainingServer


@pytest.mark.asyncio
async def test_drain_rejects_new_work_but_waits_for_existing_cleanup() -> None:
    drain = RequestDrain()
    entered, release = asyncio.Event(), asyncio.Event()

    async def application(scope: Scope, receive: object, send: object) -> None:
        entered.set()
        await release.wait()

    middleware = RequestDrainMiddleware(application, drain)
    messages: list[Message] = []

    async def send(message: Message) -> None:
        messages.append(message)

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    active = asyncio.create_task(
        middleware(cast(Scope, {"type": "http", "path": "/download"}), receive, send)
    )
    await entered.wait()
    server = DrainingServer(uvicorn.Config(middleware), drain)
    server.handle_exit(signal.SIGTERM, None)
    assert server.should_exit and drain.draining and drain.active == 1
    assert REQUEST_GRACE_SECONDS + 2 < 10
    assert not await drain.wait_closed(0.01)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=middleware), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/health/ready")
        assert response.status_code == 503 and response.headers["Retry-After"] == "3"
        assert (await client.get("/api/v1/health/live")).status_code == 200
    await middleware(cast(Scope, {"type": "websocket", "path": "/events"}), receive, send)
    assert messages == [{"type": "websocket.close", "code": 1012}]
    release.set()
    await active
    assert await drain.wait_closed(0.01) and drain.active == 0


@pytest.mark.asyncio
async def test_cancelled_request_stays_tracked_until_its_finally_finishes() -> None:
    drain = RequestDrain()
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def application(scope: Scope, receive: object, send: object) -> None:
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            cleaned.set()

    middleware = RequestDrainMiddleware(application, drain)

    async def send(message: Message) -> None:
        pass

    async def receive() -> Message:
        return {"type": "http.request"}

    task = asyncio.create_task(middleware(cast(Scope, {"type": "http"}), receive, send))
    await entered.wait()
    drain.begin()
    task.cancel()
    assert await drain.wait_closed(1)
    assert cleaned.is_set() and drain.active == 0
    with pytest.raises(asyncio.CancelledError):
        await task
