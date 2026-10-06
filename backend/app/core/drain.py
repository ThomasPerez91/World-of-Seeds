"""Bounded request cleanup during a single-process API restart."""

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

CLEANUP_TIMEOUT_SECONDS = 2.0


class RequestDrain:
    def __init__(self) -> None:
        self.draining = False
        self.active = 0
        self._closed = asyncio.Event()
        self._closed.set()

    def begin(self) -> None:
        self.draining = True

    def enter(self) -> None:
        self.active += 1
        self._closed.clear()

    def leave(self) -> None:
        self.active -= 1
        if not self.active:
            self._closed.set()

    async def wait_closed(self, budget_seconds: float = CLEANUP_TIMEOUT_SECONDS) -> bool:
        try:
            async with asyncio.timeout(budget_seconds):
                await self._closed.wait()
        except TimeoutError:
            return False
        return True


class RequestDrainMiddleware:
    def __init__(self, app: ASGIApp, drain: RequestDrain) -> None:
        self.app = app
        self.drain = drain

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        if self.drain.draining:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1012})
            elif scope["path"] == "/api/v1/health/live":
                await JSONResponse({"status": "ok"})(scope, receive, send)
            else:
                await JSONResponse(
                    {"detail": "Service unavailable"},
                    status_code=503,
                    headers={"Retry-After": "3"},
                )(scope, receive, send)
            return
        self.drain.enter()
        try:
            await self.app(scope, receive, send)
        finally:
            self.drain.leave()
