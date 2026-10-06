"""Production API entrypoint: finish short requests, then cancel and clean up."""

import os
import socket
from types import FrameType

import uvicorn

from app.core.drain import RequestDrain

# Docker Compose's unchanged API stop budget is 10s. Leave time for SQL cleanup
# (2s), lifespan shutdown and process exit after the 5s request grace period.
REQUEST_GRACE_SECONDS = 5


class DrainingServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, drain: RequestDrain) -> None:
        super().__init__(config)
        self._drain = drain

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._drain.begin()
        super().handle_exit(sig, frame)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._drain.begin()
        await super().shutdown(sockets)


def main() -> None:
    from app.main import app

    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8000,
        log_level=os.environ.get("UVICORN_LOG_LEVEL", "info"),
        access_log=False,
        server_header=False,
        timeout_graceful_shutdown=REQUEST_GRACE_SECONDS,
    )
    DrainingServer(config, app.state.request_drain).run()


if __name__ == "__main__":
    main()
