from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class TorrentEventType(StrEnum):
    REQUESTED = "torrent.requested"
    STARTED = "torrent.started"
    PAUSED = "torrent.paused"
    STALLED = "torrent.stalled"
    RESUMED = "torrent.resumed"
    READY = "torrent.ready"
    RETENTION_EXTENDED = "torrent.retention_extended"
    FAILED = "torrent.failed"
    CANCELLED = "torrent.cancelled"
    EXPIRED = "torrent.expired"


@dataclass(frozen=True, slots=True)
class TorrentRealtimeEvent:
    event_type: TorrentEventType
    request_id: uuid.UUID
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.occurred_at.utcoffset() is None:
            raise ValueError("torrent event timestamp must be timezone-aware")

    def payload(self) -> dict[str, str]:
        return {
            "type": self.event_type.value,
            "request_id": str(self.request_id),
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
        }

    def encode(self) -> str:
        return json.dumps(self.payload(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def decode(cls, raw: str | bytes) -> TorrentRealtimeEvent:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if len(raw) > 512:
            raise ValueError("torrent event payload is too large")
        payload: Any = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {
            "occurred_at",
            "request_id",
            "type",
        }:
            raise ValueError("torrent event payload is invalid")
        if not all(isinstance(payload[key], str) for key in payload):
            raise ValueError("torrent event payload fields are invalid")
        event_type = TorrentEventType(payload["type"])
        request_id = uuid.UUID(payload["request_id"])
        occurred_at = datetime.fromisoformat(payload["occurred_at"])
        return cls(event_type, request_id, occurred_at)
