"""Safe torrent ingestion and V2 managed-torrent domain helpers."""

from app.torrents.deduplication import (
    ManagedTorrentRequestResult,
    TorrentDeduplicationError,
    TorrentDeduplicationRaceError,
    TorrentMetadataConflictError,
    TorrentRequestOwnerError,
    create_or_get_torrent_request,
)
from app.torrents.metainfo import (
    ParsedTorrent,
    TorrentValidationError,
    normalize_torrent,
    sanitize_torrent,
)
from app.torrents.tracker_activity import (
    TrackerActivityError,
    assign_managed_torrent_account_refs,
    record_tracker_activity,
)

__all__ = [
    "ManagedTorrentRequestResult",
    "ParsedTorrent",
    "TorrentDeduplicationError",
    "TorrentDeduplicationRaceError",
    "TorrentMetadataConflictError",
    "TorrentRequestOwnerError",
    "TorrentValidationError",
    "TrackerActivityError",
    "assign_managed_torrent_account_refs",
    "create_or_get_torrent_request",
    "normalize_torrent",
    "sanitize_torrent",
    "record_tracker_activity",
]
