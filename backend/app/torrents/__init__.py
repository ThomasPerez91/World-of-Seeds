"""Safe torrent ingestion and V2 managed-torrent domain helpers."""

from app.torrents.deduplication import (
    ManagedTorrentRequestResult,
    TorrentDeduplicationError,
    TorrentDeduplicationRaceError,
    TorrentMetadataConflictError,
    TorrentPurgeInProgressError,
    TorrentRequestOwnerError,
    create_or_get_torrent_request,
)
from app.torrents.lifecycle import (
    PURGE_TORRENT_JOB,
    ExpiredTorrentRequest,
    TorrentCancellationResult,
    TorrentExpirationResult,
    cancel_owned_torrent_request,
    expire_ready_torrents_batch,
    extend_ready_torrent_retention,
    retention_days_for_popularity,
)
from app.torrents.manifest import (
    TorrentManifestChangedError,
    TorrentManifestError,
    TorrentManifestPage,
    TorrentManifestUnavailableError,
    TorrentManifestWriteResult,
    list_torrent_manifest,
    replace_torrent_manifest,
)
from app.torrents.metainfo import (
    ParsedTorrent,
    TorrentContentFile,
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
    "ExpiredTorrentRequest",
    "ParsedTorrent",
    "PURGE_TORRENT_JOB",
    "TorrentContentFile",
    "TorrentCancellationResult",
    "TorrentExpirationResult",
    "TorrentDeduplicationError",
    "TorrentDeduplicationRaceError",
    "TorrentMetadataConflictError",
    "TorrentPurgeInProgressError",
    "TorrentManifestChangedError",
    "TorrentManifestError",
    "TorrentManifestPage",
    "TorrentManifestUnavailableError",
    "TorrentManifestWriteResult",
    "TorrentRequestOwnerError",
    "TorrentValidationError",
    "TrackerActivityError",
    "assign_managed_torrent_account_refs",
    "cancel_owned_torrent_request",
    "create_or_get_torrent_request",
    "expire_ready_torrents_batch",
    "extend_ready_torrent_retention",
    "list_torrent_manifest",
    "normalize_torrent",
    "sanitize_torrent",
    "record_tracker_activity",
    "retention_days_for_popularity",
    "replace_torrent_manifest",
]
