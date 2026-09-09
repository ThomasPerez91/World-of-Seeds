import asyncio
from collections.abc import Awaitable, Callable

from starlette.types import Message, Receive, Scope, Send

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class ArchiveDownloadQueueMiddleware:
    """Serialize streamed fallback ZIP responses without turning contention into HTTP 429.

    The ZIP builder is intentionally bounded to one stream per API process because it performs
    synchronous archive work against the shared content store. Requests beyond that capacity wait
    here and enter the existing archive route only when the previous response has finished.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._slot = asyncio.Semaphore(1)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._is_archive_request(scope):
            await self.app(scope, receive, send)
            return

        await self._slot.acquire()
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self._slot.release()

        async def queued_send(message: Message) -> None:
            try:
                await send(message)
            finally:
                if message["type"] == "http.response.body" and not message.get("more_body", False):
                    release()

        try:
            await self.app(scope, receive, queued_send)
        finally:
            release()

    @staticmethod
    def _is_archive_request(scope: Scope) -> bool:
        if scope["type"] != "http" or scope.get("method") != "GET":
            return False
        path = scope.get("path", "")
        return path.startswith("/api/v2/torrents/") and path.endswith("/download-archive")