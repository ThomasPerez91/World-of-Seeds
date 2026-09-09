import os
from datetime import UTC, datetime

import pytest

from app.http_downloads import (
    ByteRange,
    OpenedDownload,
    RangeNotSatisfiableError,
    if_range_matches,
    parse_range_header,
    stream_download,
)


@pytest.mark.parametrize(
    ("header", "size", "expected"),
    [
        ("bytes=0-3", 10, ByteRange(0, 3)),
        ("bytes=4-", 10, ByteRange(4, 9)),
        ("bytes=-3", 10, ByteRange(7, 9)),
        ("bytes=7-50", 10, ByteRange(7, 9)),
        ("bytes=-50", 10, ByteRange(0, 9)),
    ],
)
def test_parse_range_header_accepts_single_bounded_ranges(
    header: str,
    size: int,
    expected: ByteRange,
) -> None:
    assert parse_range_header(header, size) == expected


@pytest.mark.parametrize(
    "header",
    [
        "bytes=10-",
        "bytes=8-2",
        "bytes=0-1,4-5",
        "bytes=-0",
        "bytes=-",
        "items=0-1",
        f"bytes={'9' * 21}-",
    ],
)
def test_parse_range_header_rejects_invalid_or_unsatisfiable_ranges(header: str) -> None:
    with pytest.raises(RangeNotSatisfiableError):
        parse_range_header(header, 10)


def test_empty_file_has_no_satisfiable_range() -> None:
    with pytest.raises(RangeNotSatisfiableError):
        parse_range_header("bytes=0-", 0)


def test_if_range_matches_strong_etag_and_http_date(tmp_path) -> None:
    path = tmp_path / "download.bin"
    path.write_bytes(b"content")
    descriptor = os.open(path, os.O_RDONLY)
    download = OpenedDownload(
        file_descriptor=descriptor,
        name=path.name,
        size=7,
        modified_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        media_type="application/octet-stream",
        etag='"etag"',
    )
    try:
        assert if_range_matches('"etag"', download) is True
        assert if_range_matches('"stale"', download) is False
        assert if_range_matches('W/"etag"', download) is False
        assert if_range_matches("Wed, 09 Sep 2026 12:00:00 GMT", download) is True
        assert if_range_matches("Wed, 09 Sep 2026 11:59:59 GMT", download) is False
    finally:
        download.close()


@pytest.mark.asyncio
async def test_stream_download_is_chunked_and_closes_descriptor(tmp_path) -> None:
    path = tmp_path / "download.bin"
    path.write_bytes(b"0123456789")
    descriptor = os.open(path, os.O_RDONLY)
    download = OpenedDownload(
        file_descriptor=descriptor,
        name=path.name,
        size=10,
        modified_at=datetime.now(UTC),
        media_type="application/octet-stream",
        etag='"etag"',
    )

    chunks = [chunk async for chunk in stream_download(download, start=2, length=6, chunk_size=2)]

    assert chunks == [b"23", b"45", b"67"]
    with pytest.raises(OSError):
        os.fstat(descriptor)
