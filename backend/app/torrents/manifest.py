from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ManagedTorrent, TorrentFile
from app.models.base import utc_now
from app.torrents.metainfo import MAX_TORRENT_FILES, TorrentContentFile

MAX_MANIFEST_PAGE_SIZE = 500
_MAX_BIGINT = 2**63 - 1


class TorrentManifestError(ValueError):
    """The proposed manifest is invalid or inconsistent with its managed torrent."""


class TorrentManifestUnavailableError(TorrentManifestError):
    """No validated manifest exists for the managed torrent."""


class TorrentManifestChangedError(TorrentManifestError):
    """The caller's manifest version no longer matches the authoritative snapshot."""


@dataclass(frozen=True, slots=True)
class TorrentManifestWriteResult:
    version: int
    checksum: str
    file_count: int
    total_size: int
    changed: bool


@dataclass(frozen=True, slots=True)
class TorrentManifestPage:
    managed_torrent_id: uuid.UUID
    version: int
    checksum: str
    file_count: int
    total_size: int
    offset: int
    limit: int
    items: tuple[TorrentContentFile, ...]


async def replace_torrent_manifest(
    session: AsyncSession,
    managed_torrent_id: uuid.UUID,
    files: Sequence[TorrentContentFile],
    *,
    now: datetime | None = None,
) -> TorrentManifestWriteResult:
    entries = _validated_entries(files)
    checksum = _checksum(entries)
    total_size = sum(entry.size for entry in entries)
    timestamp = now or utc_now()
    torrent = await session.get(ManagedTorrent, managed_torrent_id, with_for_update=True)
    if torrent is None:
        raise TorrentManifestError("managed torrent is missing")
    if total_size != torrent.total_size:
        raise TorrentManifestError("manifest total does not match the managed torrent")
    if (
        torrent.manifest_version >= 1
        and torrent.manifest_checksum == checksum
        and torrent.manifest_file_count == len(entries)
        and torrent.manifest_total_size == total_size
    ):
        stored_count = await session.scalar(
            select(func.count())
            .select_from(TorrentFile)
            .where(TorrentFile.managed_torrent_id == managed_torrent_id)
        )
        if stored_count == len(entries):
            return TorrentManifestWriteResult(
                torrent.manifest_version,
                checksum,
                len(entries),
                total_size,
                False,
            )

    await session.execute(
        delete(TorrentFile).where(TorrentFile.managed_torrent_id == managed_torrent_id)
    )
    values = [
        {
            "id": uuid.uuid4(),
            "managed_torrent_id": managed_torrent_id,
            "file_index": entry.file_index,
            "relative_path": entry.relative_path,
            "size": entry.size,
        }
        for entry in entries
    ]
    for start in range(0, len(values), 1_000):
        await session.execute(insert(TorrentFile), values[start : start + 1_000])
    torrent.manifest_version += 1
    torrent.manifest_checksum = checksum
    torrent.manifest_file_count = len(entries)
    torrent.manifest_total_size = total_size
    torrent.updated_at = timestamp
    await session.flush()
    return TorrentManifestWriteResult(
        torrent.manifest_version,
        checksum,
        len(entries),
        total_size,
        True,
    )


async def list_torrent_manifest(
    session: AsyncSession,
    managed_torrent_id: uuid.UUID,
    *,
    offset: int = 0,
    limit: int = 100,
    expected_version: int | None = None,
) -> TorrentManifestPage:
    if type(offset) is not int or offset < 0:
        raise TorrentManifestError("manifest offset is invalid")
    if type(limit) is not int or not 1 <= limit <= MAX_MANIFEST_PAGE_SIZE:
        raise TorrentManifestError("manifest limit is invalid")
    if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
        raise TorrentManifestError("manifest version is invalid")
    torrent = await session.get(ManagedTorrent, managed_torrent_id)
    if torrent is None or torrent.manifest_version < 1 or torrent.manifest_checksum is None:
        raise TorrentManifestUnavailableError("torrent manifest is unavailable")
    if expected_version is not None and expected_version != torrent.manifest_version:
        raise TorrentManifestChangedError("torrent manifest changed")
    rows = tuple(
        (
            await session.scalars(
                select(TorrentFile)
                .where(TorrentFile.managed_torrent_id == managed_torrent_id)
                .order_by(TorrentFile.file_index)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    expected_items = min(limit, max(0, torrent.manifest_file_count - offset))
    if len(rows) != expected_items:
        raise TorrentManifestChangedError("torrent manifest rows changed")
    return TorrentManifestPage(
        managed_torrent_id=managed_torrent_id,
        version=torrent.manifest_version,
        checksum=torrent.manifest_checksum,
        file_count=torrent.manifest_file_count,
        total_size=torrent.manifest_total_size,
        offset=offset,
        limit=limit,
        items=tuple(
            TorrentContentFile(row.file_index, row.relative_path, row.size) for row in rows
        ),
    )


def _validated_entries(files: Sequence[TorrentContentFile]) -> tuple[TorrentContentFile, ...]:
    entries = tuple(files)
    if not 1 <= len(entries) <= MAX_TORRENT_FILES:
        raise TorrentManifestError("manifest file count is invalid")
    paths: set[str] = set()
    total = 0
    for expected_index, entry in enumerate(entries):
        if not isinstance(entry, TorrentContentFile) or entry.file_index != expected_index:
            raise TorrentManifestError("manifest indexes must be contiguous")
        path = entry.relative_path
        parsed = PurePosixPath(path)
        if (
            not path
            or len(path) > 4096
            or path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or parsed.is_absolute()
            or path != parsed.as_posix()
            or any(component in {"", ".", ".."} for component in parsed.parts)
            or path in paths
        ):
            raise TorrentManifestError("manifest path is invalid")
        if type(entry.size) is not int or not 0 <= entry.size <= _MAX_BIGINT:
            raise TorrentManifestError("manifest size is invalid")
        total += entry.size
        if total > _MAX_BIGINT:
            raise TorrentManifestError("manifest total is invalid")
        paths.add(path)
    return entries


def _checksum(entries: Sequence[TorrentContentFile]) -> str:
    digest = hashlib.sha256(b"world-of-seeds-v2-manifest\x00")
    for entry in entries:
        path = entry.relative_path.encode("utf-8")
        digest.update(entry.file_index.to_bytes(8, "big"))
        digest.update(entry.size.to_bytes(8, "big"))
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
    return digest.hexdigest()
