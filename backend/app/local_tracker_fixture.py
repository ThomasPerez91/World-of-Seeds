"""Development-only NewGreedy health fixture for the local V2 stack."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/api/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = json.dumps({"total": 0}, separators=(",", ":")).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def main() -> None:
    if os.environ.get("WOS_ENVIRONMENT") != "development":
        raise RuntimeError("local tracker fixture is restricted to development")
    ThreadingHTTPServer(("0.0.0.0", 8080), _Handler).serve_forever()


if __name__ == "__main__":
    main()
