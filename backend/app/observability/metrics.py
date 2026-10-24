from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@dataclass(slots=True)
class _Histogram:
    """Cumulative counters: monotonic for the life of the process, as Prometheus requires."""

    buckets: list[int] = field(default_factory=lambda: [0] * len(_DURATION_BUCKETS))
    total: float = 0.0
    count: int = 0

    def observe(self, value: float) -> None:
        for index, bound in enumerate(_DURATION_BUCKETS):
            if value <= bound:
                self.buckets[index] += 1
        self.total += value
        self.count += 1


class MetricsRegistry:
    """Small in-process API collector with fixed labels and Prometheus text output."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self._durations: dict[tuple[str, str], _Histogram] = defaultdict(_Histogram)

    def observe_request(self, method: str, route: str, status_code: int, duration: float) -> None:
        normalized_method = method if method in _METHODS else "OTHER"
        normalized_route = route if route.startswith("/api/") and len(route) <= 128 else "unmatched"
        status_class = f"{max(0, min(status_code // 100, 9))}xx"
        safe_duration = duration if math.isfinite(duration) and duration >= 0 else 0.0
        with self._lock:
            self._requests[(normalized_method, normalized_route, status_class)] += 1
            self._durations[(normalized_method, normalized_route)].observe(safe_duration)

    def render_api(self) -> list[str]:
        lines = [
            "# HELP wos_api_requests_total HTTP requests by bounded route template.",
            "# TYPE wos_api_requests_total counter",
        ]
        with self._lock:
            requests = dict(self._requests)
            durations = {
                key: (tuple(value.buckets), value.total, value.count)
                for key, value in self._durations.items()
            }
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
        for (method, route), (buckets, total, count) in sorted(durations.items()):
            labels = f'method="{_label(method)}",route="{_label(route)}"'
            for bound, bucket_count in zip(_DURATION_BUCKETS, buckets, strict=True):
                lines.append(
                    f'wos_api_request_duration_seconds_bucket{{{labels},le="{bound}"}} '
                    f"{bucket_count}"
                )
            lines.append(f'wos_api_request_duration_seconds_bucket{{{labels},le="+Inf"}} {count}')
            lines.append(f"wos_api_request_duration_seconds_sum{{{labels}}} {total:.9f}")
            lines.append(f"wos_api_request_duration_seconds_count{{{labels}}} {count}")
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
