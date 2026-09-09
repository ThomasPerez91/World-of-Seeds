"""Compatibility re-export for HTTP download primitives.

The per-user filesystem downloader was retired by UX-05B. Torrent READY downloads
use the neutral primitives from :mod:`app.http_downloads`.
"""

from app.http_downloads import (
    DOWNLOAD_CHUNK_SIZE,
    ByteRange,
    DownloadStreamingResponse,
    OpenedDownload,
    RangeNotSatisfiableError,
    if_range_matches,
    parse_range_header,
    stream_download,
)

__all__ = [
    "DOWNLOAD_CHUNK_SIZE",
    "ByteRange",
    "DownloadStreamingResponse",
    "OpenedDownload",
    "RangeNotSatisfiableError",
    "if_range_matches",
    "parse_range_header",
    "stream_download",
]
