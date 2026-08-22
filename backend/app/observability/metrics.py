from __future__ import annotations

import math
import threading
import time
from collections import defaultdict

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricsRegistry:
    """Small in-process API collector with fixed labels and Prometheus text output."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self._durations: dict[tuple[str, str], list[float]] = defaultdict(list)

    def observe_request(self, method: str, route: str, status_code: int, duration: float) -> None:
        normalized_method = method if method in _METHODS else "OTHER"
        normalized_route = route if route.startswith("/api/") and len(route) <= 128 else "unmatched"
        status_class = f"{max(0, min(status_code // 100, 9))}xx"
        safe_duration = duration if math.isfinite(duration) and duration >= 0 else 0.0
        with self._lock:
            self._requests[(normalized_method, normalized_route, status_class)] += 1
            samples = self._durations[(normalized_method, normalized_route)]
            samples.append(safe_duration)
            if len(samples) > 10_000:
                del samples[: len(samples) - 10_000]

    def render_api(self) -> list[str]:
        lines = [
            "# HELP wos_api_requests_total HTTP requests by bounded route template.",
            "# TYPE wos_api_requests_total counter",
        ]
        with self._lock:
            requests = dict(self._requests)
            durations = {key: tuple(values) for key, values in self._durations.items()}
        for (method, route, status_class), value in sorted(requests.items()):
            lines.append(
                f'wos_api_requests_total{{method="{_label(method)}",route="{_label(route)}",'
                f'status_class="{status_class}"}} {value}'
            )
        lines.extend(
            [
                "# HELP wos_api_request_duration_seconds HTTP request duration.",
                "# TYPE wos_api_request_duration_seconds histogram",
            ]
        )
        for (method, route), values in sorted(durations.items()):
            labels = f'method="{_label(method)}",route="{_label(route)}"'
            for bucket in _DURATION_BUCKETS:
                count = sum(value <= bucket for value in values)
                lines.append(
                    f'wos_api_request_duration_seconds_bucket{{{labels},le="{bucket}"}} {count}'
                )
            lines.append(
                f'wos_api_request_duration_seconds_bucket{{{labels},le="+Inf"}} {len(values)}'
            )
            lines.append(f"wos_api_request_duration_seconds_sum{{{labels}}} {sum(values):.9f}")
            lines.append(f"wos_api_request_duration_seconds_count{{{labels}}} {len(values)}")
        return lines


class RequestMetricsMiddleware:
    def __init__(self, app: ASGIApp, *, registry: MetricsRegistry) -> None:
        self.app = app
        self.registry = registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status_code = 500

        async def observe_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, observe_send)
        finally:
            route = scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            self.registry.observe_request(
                str(scope.get("method", "OTHER")),
                str(route_path),
                status_code,
                time.perf_counter() - started,
            )
