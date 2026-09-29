"""In-process scheduling of HTTP file streams (production runs one API process)."""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field


class DownloadWaitingLimit(RuntimeError):
    """One account already has two reduced-rate transfers waiting."""


@dataclass(slots=True)
class _Transfer:
    user_id: uuid.UUID
    total_bytes: int
    sequence: int
    joined_at: float
    fast: bool
    fast_since: float = 0.0
    next_slow_at: float = 0.0
    sent_bytes: int = 0
    observed_bytes: int = 0
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
    FAST_QUANTUM_SECONDS = 120

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

    async def register(self, lease_id: uuid.UUID, user_id: uuid.UUID, total_bytes: int) -> None:
        if total_bytes < 0:
            raise ValueError("download size cannot be negative")
        async with self._lock:
            if lease_id in self._transfers:
                raise ValueError("download is already registered")
            fast = sum(transfer.fast for transfer in self._transfers.values()) < self._fast_limit
            if (
                not fast
                and sum(
                    transfer.user_id == user_id and not transfer.fast
                    for transfer in self._transfers.values()
                )
                >= self.WAITING_PER_USER
            ):
                raise DownloadWaitingLimit("user download waiting limit reached")
            self._sequence += 1
            now = self._clock()
            self._transfers[lease_id] = _Transfer(
                user_id=user_id,
                total_bytes=total_bytes,
                sequence=self._sequence,
                joined_at=now,
                fast=fast,
                fast_since=now if fast else 0.0,
            )

    async def unregister(self, lease_id: uuid.UUID) -> None:
        async with self._lock:
            departing = self._transfers.pop(lease_id, None)
            if departing is None or not departing.fast:
                return
            waiting = [entry for entry in self._transfers.values() if not entry.fast]
            if waiting:
                now = self._clock()
                next_entry = max(
                    waiting,
                    key=lambda entry: (self._priority(entry, now), -entry.sequence),
                )
                self._promote(next_entry, now)

    async def observe_upload(self, bytes_per_second: float | None) -> None:
        """Rebalance against host-wide egress, including qB and other services.

        Missing telemetry freezes the last decision; one sample changes capacity
        by at most a quarter, so noisy measurements cannot empty the fast pool.
        """
        if bytes_per_second is None or not math.isfinite(bytes_per_second):
            return
        async with self._lock:
            now = self._clock()
            fast = [entry for entry in self._transfers.values() if entry.fast]
            waiting = [entry for entry in self._transfers.values() if not entry.fast]
            utilization = max(0.0, bytes_per_second) / self._uplink_bytes_per_second
            if waiting and utilization < self.LOWER_UTILIZATION:
                increase = max(1, math.ceil(self._fast_limit / 4))
                self._fast_limit = min(self.MAX_FAST_LIMIT, self._fast_limit + increase)
                for entry in sorted(
                    waiting,
                    key=lambda item: (-self._priority(item, now), item.sequence),
                )[: max(0, self._fast_limit - len(fast))]:
                    self._promote(entry, now)
            elif fast and utilization > self.UPPER_UTILIZATION:
                decrease = max(1, math.ceil(self._fast_limit / 5))
                target = max(self.INITIAL_FAST_LIMIT, self._fast_limit - decrease)
                # Demote the least productive eligible stream first. A slow
                # receiver must not reserve a fast lane indefinitely.
                fast_count = len(fast)
                for entry in sorted(
                    fast,
                    key=lambda item: (item.sent_bytes - item.observed_bytes, item.sequence),
                ):
                    if fast_count <= target:
                        break
                    if (
                        sum(
                            item.user_id == entry.user_id and not item.fast
                            for item in self._transfers.values()
                        )
                        >= self.WAITING_PER_USER
                    ):
                        continue
                    entry.fast = False
                    entry.joined_at = now
                    entry.next_slow_at = now
                    entry.promoted.clear()
                    fast_count -= 1
                self._fast_limit = max(target, fast_count)
            for entry in self._transfers.values():
                entry.observed_bytes = entry.sent_bytes

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
                if entry.fast and now - entry.fast_since >= self.FAST_QUANTUM_SECONDS:
                    waiting = [item for item in self._transfers.values() if not item.fast]
                    # The two-waiting limit applies to new admissions. Rotation
                    # can temporarily add a third preempted stream so another
                    # account still gets a turn.
                    if waiting:
                        other_accounts = [item for item in waiting if item.user_id != entry.user_id]
                        next_entry = max(
                            other_accounts or waiting,
                            key=lambda item: (self._priority(item, now), -item.sequence),
                        )
                        entry.fast = False
                        entry.joined_at = now
                        entry.next_slow_at = now
                        entry.promoted.clear()
                        self._promote(next_entry, now)
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
        async with self._lock:
            if entry := self._transfers.get(lease_id):
                entry.sent_bytes += byte_count

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
                "fast_limit": self._fast_limit,
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
        entry.fast = True
        entry.fast_since = now
        entry.promoted.set()
