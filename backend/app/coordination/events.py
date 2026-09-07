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
    QUEUE_CHANGED = "torrent.queue_changed"
    FAILED = "torrent.failed"
    CANCELLED = "torrent.cancelled"
    EXPIRED = "torrent.expired"


@dataclass(frozen=True, slots=True)
class TorrentRealtimeEvent:
    event_type: TorrentEventType
    request_id: uuid.UUID | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.occurred_at.utcoffset() is None:
            raise ValueError("torrent event timestamp must be timezone-aware")
        if (self.event_type is TorrentEventType.QUEUE_CHANGED) != (self.request_id is None):
            raise ValueError("only a global queue event may omit the request ID")

    def payload(self) -> dict[str, str]:
        payload = {
            "type": self.event_type.value,
            "occurred_at": self.occurred_at.astimezone(UTC).isoformat(),
        }
        if self.request_id is not None:
            payload["request_id"] = str(self.request_id)
        return payload

    def encode(self) -> str:
        return json.dumps(self.payload(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def decode(cls, raw: str | bytes) -> TorrentRealtimeEvent:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if len(raw) > 512:
            raise ValueError("torrent event payload is too large")
        payload: Any = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("torrent event payload is invalid")
        raw_event_type = payload.get("type")
        if not isinstance(raw_event_type, str):
            raise ValueError("torrent event type is invalid")
        event_type = TorrentEventType(raw_event_type)
        expected_keys = (
            {"occurred_at", "type"}
            if event_type is TorrentEventType.QUEUE_CHANGED
            else {"occurred_at", "request_id", "type"}
        )
        if set(payload) != expected_keys or not all(
            isinstance(payload[key], str) for key in payload
        ):
            raise ValueError("torrent event payload fields are invalid")
        request_id = (
            None
            if event_type is TorrentEventType.QUEUE_CHANGED
            else uuid.UUID(payload["request_id"])
        )
        occurred_at = datetime.fromisoformat(payload["occurred_at"])
        return cls(event_type, request_id, occurred_at)
