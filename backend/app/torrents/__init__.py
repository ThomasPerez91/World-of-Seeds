"""Safe torrent ingestion and V2 managed-torrent domain helpers."""

from app.torrents.deduplication import (
    ManagedTorrentRequestResult,
    TorrentDeduplicationError,
    TorrentDeduplicationRaceError,
    TorrentMetadataConflictError,
    TorrentRequestOwnerError,
    create_or_get_torrent_request,
)
from app.torrents.metainfo import ParsedTorrent, TorrentValidationError, normalize_torrent

__all__ = [
    "ManagedTorrentRequestResult",
    "ParsedTorrent",
    "TorrentDeduplicationError",
    "TorrentDeduplicationRaceError",
    "TorrentMetadataConflictError",
    "TorrentRequestOwnerError",
    "TorrentValidationError",
    "create_or_get_torrent_request",
    "normalize_torrent",
]
