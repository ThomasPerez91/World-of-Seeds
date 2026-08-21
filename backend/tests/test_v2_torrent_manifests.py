import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ManagedTorrent, TorrentFile
from app.torrents import (
    TorrentContentFile,
    TorrentManifestChangedError,
    TorrentManifestError,
    TorrentValidationError,
    list_torrent_manifest,
    replace_torrent_manifest,
    sanitize_torrent,
)


def _bencode(value: object) -> bytes:
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(_bencode(key) + _bencode(value[key]) for key in sorted(value)) + b"e"
    raise TypeError(value)


def _multi_file_torrent(*, paths: list[list[bytes]] | None = None) -> bytes:
    file_paths = paths or [[b"Season 1", b"Episode 1.mkv"], [b"cover.jpg"]]
    files = [{b"length": index + 5, b"path": path} for index, path in enumerate(file_paths)]
    return _bencode(
        {
            b"announce": b"https://c411.org/private/value",
            b"info": {
                b"files": files,
                b"name": b"My Show",
                b"piece length": 16_384,
                b"pieces": b"p" * 20,
            },
        }
    )


def test_sanitized_metainfo_builds_canonical_physical_paths() -> None:
    parsed = sanitize_torrent(
        _multi_file_torrent(),
        allowed_tracker_hosts=["c411.org"],
        max_total_size=1_000,
    )

    assert parsed.total_size == 11
    assert parsed.files == (
        TorrentContentFile(0, "My Show/Season 1/Episode 1.mkv", 5),
        TorrentContentFile(1, "My Show/cover.jpg", 6),
    )


@pytest.mark.parametrize(
    "paths",
    [
        [[b"duplicate"], [b"duplicate"]],
        [[b"valid"], [b"\xff"]],
        [[b".."]],
    ],
)
def test_metainfo_rejects_unsafe_or_duplicate_manifest_paths(paths: list[list[bytes]]) -> None:
    with pytest.raises(TorrentValidationError):
        sanitize_torrent(
            _multi_file_torrent(paths=paths),
            allowed_tracker_hosts=["c411.org"],
            max_total_size=1_000,
        )


async def _managed(session: AsyncSession, *, total_size: int = 30) -> ManagedTorrent:
    torrent = ManagedTorrent(
        info_hash=uuid.uuid4().hex + "00000000",
        name="manifest",
        total_size=total_size,
    )
    session.add(torrent)
    await session.flush()
    return torrent


@pytest.mark.asyncio
async def test_manifest_replace_is_versioned_and_idempotent(db_session: AsyncSession) -> None:
    torrent = await _managed(db_session)
    files = (
        TorrentContentFile(0, "manifest/a.bin", 10),
        TorrentContentFile(1, "manifest/b.bin", 20),
    )

    first = await replace_torrent_manifest(db_session, torrent.id, files)
    replay = await replace_torrent_manifest(db_session, torrent.id, files)

    assert first.version == replay.version == 1
    assert first.changed is True
    assert replay.changed is False
    assert len(first.checksum) == 64
    assert await db_session.scalar(select(func.count()).select_from(TorrentFile)) == 2

    changed = await replace_torrent_manifest(
        db_session,
        torrent.id,
        (
            TorrentContentFile(0, "manifest/renamed.bin", 10),
            TorrentContentFile(1, "manifest/b.bin", 20),
        ),
    )
    assert changed.version == 2
    assert changed.checksum != first.checksum


@pytest.mark.asyncio
async def test_manifest_listing_is_bounded_and_detects_version_change(
    db_session: AsyncSession,
) -> None:
    torrent = await _managed(db_session, total_size=6)
    await replace_torrent_manifest(
        db_session,
        torrent.id,
        tuple(TorrentContentFile(index, f"manifest/{index}.bin", index + 1) for index in range(3)),
    )

    page = await list_torrent_manifest(db_session, torrent.id, offset=1, limit=1)
    assert page.version == 1
    assert page.file_count == 3
    assert page.total_size == 6
    assert page.items == (TorrentContentFile(1, "manifest/1.bin", 2),)

    await replace_torrent_manifest(
        db_session,
        torrent.id,
        (
            TorrentContentFile(0, "manifest/a.bin", 1),
            TorrentContentFile(1, "manifest/b.bin", 2),
            TorrentContentFile(2, "manifest/c.bin", 3),
        ),
    )
    with pytest.raises(TorrentManifestChangedError):
        await list_torrent_manifest(db_session, torrent.id, expected_version=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "files",
    [
        (),
        (TorrentContentFile(1, "manifest/a", 30),),
        (TorrentContentFile(0, "../escape", 30),),
        (TorrentContentFile(0, "/absolute", 30),),
        (TorrentContentFile(0, "manifest/a", 29),),
    ],
)
async def test_manifest_rejects_invalid_entries_or_total(
    db_session: AsyncSession,
    files: tuple[TorrentContentFile, ...],
) -> None:
    torrent = await _managed(db_session)
    with pytest.raises(TorrentManifestError):
        await replace_torrent_manifest(db_session, torrent.id, files)
