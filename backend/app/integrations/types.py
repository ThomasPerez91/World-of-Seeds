from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ServiceKey = Literal["newgreedy", "qbittorrent"]
ServiceState = Literal["healthy", "unavailable", "unconfigured"]


@dataclass(frozen=True, slots=True)
class ServiceProbe:
    service: ServiceKey
    state: ServiceState
    latency_ms: int | None = None
    version: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class NewGreedyOverview:
    torrents: int
    downloading: int
    seeding: int
    stalled: int
    target_reached: int
    total_downloaded_bytes: int
    total_reported_uploaded_bytes: int
    total_fake_uploaded_bytes: int


@dataclass(frozen=True, slots=True)
class NewGreedyStatsReset:
    purged: int
    remaining: int


@dataclass(frozen=True, slots=True)
class NewGreedyTorrent:
    id: str
    mode: Literal["down", "seed"]
    downloaded_bytes: int
    reported_uploaded_bytes: int
    fake_uploaded_bytes: int
    ratio: float | None
    announce_count: int
    stalled: bool
    target_reached: bool
    last_announce_at: datetime | None


@dataclass(frozen=True, slots=True)
class QBittorrentTorrent:
    id: str
    name: str
    state: str
    progress: float
    size_bytes: int
    downloaded_bytes: int
    uploaded_bytes: int
    download_speed_bytes: int
    upload_speed_bytes: int
    ratio: float
    eta_seconds: int | None
    category: str | None
    tracker_host: str | None


@dataclass(frozen=True, slots=True)
class ExternalServicesSnapshot:
    checked_at: datetime
    newgreedy: ServiceProbe
    qbittorrent: ServiceProbe

    @property
    def healthy(self) -> bool:
        return self.newgreedy.state == "healthy" and self.qbittorrent.state == "healthy"
