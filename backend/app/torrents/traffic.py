"""In-process scheduling of HTTP file streams (production runs one API process)."""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Literal


class DownloadWaitingLimit(RuntimeError):
    """One account already has two reduced-rate transfers waiting."""


@dataclass(slots=True)
class _Transfer:
    user_id: uuid.UUID
    total_bytes: int
    sequence: int
    joined_at: float
    fast: bool
    next_slow_at: float = 0.0
    sent_bytes: int = 0
    last_progress_at: float = 0.0
    promoted: asyncio.Event = field(default_factory=asyncio.Event)


class DownloadTrafficScheduler:
    """Adapt fast streams to measured host egress; keep waiting streams moving.

    No response is parked in an admission queue: a waiting request can make
    progress, and promotion wakes it as soon as a fast transfer finishes.
    """

    INITIAL_FAST_LIMIT = 5
    MAX_FAST_LIMIT = 64
    LOWER_UTILIZATION = 0.80
    UPPER_UTILIZATION = 0.95
    WAITING_PER_USER = 2
    WAITING_BYTES_PER_SECOND = 1024
    WAITING_CHUNK_BYTES = 1024

    TELEMETRY_MAX_AGE_SECONDS = 45
    LATENCY_BUCKETS = (0.1, 1, 5, 15, 30, 60, 120, 300, 600)

    def __init__(
        self,
        *,
        uplink_bytes_per_second: int = 125_000_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if uplink_bytes_per_second <= 0:
            raise ValueError("uplink capacity must be positive")
        self._clock = clock
        self._uplink_bytes_per_second = uplink_bytes_per_second
        self._fast_limit = self.INITIAL_FAST_LIMIT
        self._lock = asyncio.Lock()
        self._transfers: dict[uuid.UUID, _Transfer] = {}
        self._sequence = 0
        self._admitted = 0
        self._rejected = 0
        self._peak_active = 0
        self._peak_waiting = 0
        self._outcomes = {"completed": 0, "interrupted": 0, "error": 0}
        self._bytes = {"fast": 0, "waiting": 0}
        self._latencies = {
            kind: [0.0] * (len(self.LATENCY_BUCKETS) + 2) for kind in ("fast_grant", "first_byte")
        }
        self._upload: float = 0
        self._telemetry_at: float | None = None

    async def register(self, lease_id: uuid.UUID, user_id: uuid.UUID, total_bytes: int) -> None:
        if total_bytes < 0:
            raise ValueError("download size cannot be negative")
        async with self._lock:
            if lease_id in self._transfers:
                raise ValueError("download is already registered")
            now = self._clock()
            self._fill_fast_slots(now)
            fast = sum(transfer.fast for transfer in self._transfers.values()) < self._fast_limit
            if (
                not fast
                and sum(
                    transfer.user_id == user_id and not transfer.fast
                    for transfer in self._transfers.values()
                )
                >= self.WAITING_PER_USER
            ):
                self._rejected += 1
                raise DownloadWaitingLimit("user download waiting limit reached")
            self._sequence += 1
            self._transfers[lease_id] = _Transfer(
                user_id=user_id,
                total_bytes=total_bytes,
                sequence=self._sequence,
                joined_at=now,
                fast=fast,
                last_progress_at=now,
            )
            self._admitted += 1
            self._peak_active = max(self._peak_active, len(self._transfers))
            self._peak_waiting = max(
                self._peak_waiting, sum(not e.fast for e in self._transfers.values())
            )
            if fast:
                self._observe_latency("fast_grant", 0)

    async def unregister(
        self,
        lease_id: uuid.UUID,
        *,
        outcome: Literal["completed", "interrupted", "error"] = "interrupted",
    ) -> None:
        if outcome not in self._outcomes:
            raise ValueError("invalid download outcome")
        async with self._lock:
            departing = self._transfers.pop(lease_id, None)
            if departing is not None:
                self._outcomes[outcome] += 1
                if departing.fast:
                    self._fill_fast_slots(self._clock())

    async def observe_upload(
        self, bytes_per_second: float | None, *, sample_age_seconds: float = 0
    ) -> None:
        """Rebalance against host-wide egress, including qB and other services.

        Missing telemetry freezes the last decision. A high sample reduces only
        future admissions: existing fast downloads keep their lane until they end.
        """
        if (
            bytes_per_second is None
            or not math.isfinite(bytes_per_second)
            or not math.isfinite(sample_age_seconds)
            or not 0 <= sample_age_seconds <= self.TELEMETRY_MAX_AGE_SECONDS
        ):
            return
        async with self._lock:
            self._telemetry_at = self._clock() - sample_age_seconds
            self._upload = max(0.0, bytes_per_second)
            waiting = [entry for entry in self._transfers.values() if not entry.fast]
            utilization = max(0.0, bytes_per_second) / self._uplink_bytes_per_second
            if waiting and utilization < self.LOWER_UTILIZATION:
                increase = max(1, math.ceil(self._fast_limit / 4))
                self._fast_limit = min(self.MAX_FAST_LIMIT, self._fast_limit + increase)
                self._fill_fast_slots(self._clock())
            elif utilization > self.UPPER_UTILIZATION:
                decrease = max(1, math.ceil(self._fast_limit / 5))
                self._fast_limit = max(self.INITIAL_FAST_LIMIT, self._fast_limit - decrease)

    async def next_chunk(
        self,
        lease_id: uuid.UUID,
        max_bytes: int,
    ) -> tuple[int, int]:
        """Return (chunk bytes, slow-stream cap). Global caps are work conserving."""
        if max_bytes <= 0:
            raise ValueError("chunk size must be positive")
        while True:
            async with self._lock:
                entry = self._transfers[lease_id]
                now = self._clock()
                if entry.fast:
                    return max_bytes, 0
                if now >= entry.next_slow_at:
                    size = min(max_bytes, self.WAITING_CHUNK_BYTES)
                    entry.next_slow_at = now + size / self.WAITING_BYTES_PER_SECOND
                    return size, self.WAITING_BYTES_PER_SECOND
                delay = entry.next_slow_at - now
                promoted = entry.promoted
            with suppress(TimeoutError):
                await asyncio.wait_for(promoted.wait(), timeout=delay)

    async def report(self, lease_id: uuid.UUID, byte_count: int) -> None:
        if byte_count < 0:
            raise ValueError("negative sent bytes")
        async with self._lock:
            if byte_count and (entry := self._transfers.get(lease_id)):
                now = self._clock()
                if not entry.sent_bytes:
                    self._observe_latency("first_byte", now - entry.joined_at)
                entry.sent_bytes += byte_count
                entry.last_progress_at = now
                self._bytes["fast" if entry.fast else "waiting"] += byte_count

    async def snapshot(self) -> tuple[int, int]:
        async with self._lock:
            fast = sum(entry.fast for entry in self._transfers.values())
            return fast, len(self._transfers) - fast

    async def status(self, user_id: uuid.UUID) -> dict[str, int]:
        async with self._lock:
            fast = sum(entry.fast for entry in self._transfers.values())
            mine = [entry for entry in self._transfers.values() if entry.user_id == user_id]
            return {
                "fast_streams": fast,
                "waiting_streams": len(self._transfers) - fast,
                "my_fast_streams": sum(entry.fast for entry in mine),
                "my_waiting_streams": sum(not entry.fast for entry in mine),
                # Existing fast transfers keep their lane even if the current
                # admission target was lowered by a busy uplink sample.
                "fast_limit": max(fast, self._fast_limit),
                "fast_admission_target": self._fast_limit,
                "waiting_limit_per_user": self.WAITING_PER_USER,
                "waiting_bytes_per_second": self.WAITING_BYTES_PER_SECOND,
            }

    @staticmethod
    def _size_bonus(entry: _Transfer) -> float:
        remaining = max(1, entry.total_bytes - entry.sent_bytes)
        # Short files get at most a five-minute head start. Waiting time then
        # dominates, so a large film cannot be displaced forever by small files.
        return min(300.0, max(0.0, 30.0 * math.log2((20 * 10**9) / remaining)))

    def _priority(self, entry: _Transfer, now: float) -> float:
        return now - entry.joined_at + self._size_bonus(entry)

    def _promote(self, entry: _Transfer, now: float) -> None:
        self._observe_latency("fast_grant", now - entry.joined_at)
        entry.fast = True
        entry.promoted.set()

    def _fill_fast_slots(self, now: float) -> None:
        vacancies = self._fast_limit - sum(entry.fast for entry in self._transfers.values())
        if vacancies <= 0:
            return
        waiting = (entry for entry in self._transfers.values() if not entry.fast)
        for entry in sorted(waiting, key=lambda item: (-self._priority(item, now), item.sequence))[
            :vacancies
        ]:
            self._promote(entry, now)

    def _observe_latency(self, kind: str, seconds: float) -> None:
        seconds = max(0.0, seconds)
        values = self._latencies[kind]
        for index, bucket in enumerate(self.LATENCY_BUCKETS):
            values[index] += seconds <= bucket
        values[-2] += seconds
        values[-1] += 1

    async def render_metrics(self) -> list[str]:
        """Aggregate only: no user IDs, lease IDs, paths, filenames or IP labels."""
        async with self._lock:
            now = self._clock()
            fast = [entry for entry in self._transfers.values() if entry.fast]
            waiting = [entry for entry in self._transfers.values() if not entry.fast]
            age = max(0, now - self._telemetry_at) if self._telemetry_at is not None else 0
            gauges = {
                "peak_active_streams": self._peak_active,
                "peak_waiting_streams": self._peak_waiting,
                "fast_streams": len(fast),
                "waiting_streams": len(waiting),
                "fast_admission_target": self._fast_limit,
                "waiting_oldest_age_seconds": max((now - e.joined_at for e in waiting), default=0),
                "fast_max_idle_seconds": max((now - e.last_progress_at for e in fast), default=0),
                "host_upload_bytes_per_second": self._upload,
                "uplink_capacity_bytes_per_second": self._uplink_bytes_per_second,
                "telemetry_age_seconds": age,
                "telemetry_fresh": int(
                    self._telemetry_at is not None and age <= self.TELEMETRY_MAX_AGE_SECONDS
                ),
            }
            lines: list[str] = []
            for name, value in gauges.items():
                metric = f"wos_http_download_{name}"
                lines.extend([f"# TYPE {metric} gauge", f"{metric} {value:.6f}"])
            for name, value in (
                ("admitted_total", self._admitted),
                ("waiting_rejected_total", self._rejected),
            ):
                metric = f"wos_http_download_{name}"
                lines.extend([f"# TYPE {metric} counter", f"{metric} {value}"])
            lines.append("# TYPE wos_http_download_ended_total counter")
            for outcome, count in self._outcomes.items():
                lines.append(f'wos_http_download_ended_total{{outcome="{outcome}"}} {count}')
            lines.append("# HELP wos_http_download_bytes_total Body bytes accepted by ASGI send.")
            lines.append("# TYPE wos_http_download_bytes_total counter")
            for lane, count in self._bytes.items():
                lines.append(f'wos_http_download_bytes_total{{lane="{lane}"}} {count}')
            for kind, values in self._latencies.items():
                metric = f"wos_http_download_{kind}_seconds"
                lines.append(f"# TYPE {metric} histogram")
                for bucket, bucket_count in zip(self.LATENCY_BUCKETS, values, strict=False):
                    lines.append(f'{metric}_bucket{{le="{bucket}"}} {int(bucket_count)}')
                lines.extend(
                    [
                        f'{metric}_bucket{{le="+Inf"}} {int(values[-1])}',
                        f"{metric}_sum {values[-2]:.6f}",
                        f"{metric}_count {int(values[-1])}",
                    ]
                )
            return lines
