#!/usr/bin/env python3
"""Secret-safe runtime probe for V2-33 Gate 6 transfer and manifest validation."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, func, insert, select

from app.auth.security import DUMMY_PASSWORD_HASH
from app.auth.service import issue_session
from app.core.config import get_settings
from app.core.database import session_factory
from app.models import (
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.storage import SharedContentStore, SharedContentStoreError

MANIFEST_FILE_COUNT = 50_000
MANIFEST_PAGE_SIZE = 500
SMALL_FILE_SIZE = 1024 * 1024
LARGE_FILE_SIZE = 32 * 1024 * 1024
BULK_BATCH_SIZE = 2_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "cleanup"))
    parser.add_argument("--campaign", required=True)
    return parser.parse_args()


def _username(campaign: str) -> str:
    return f"gate6-{campaign}"


def _torrent_name(campaign: str) -> str:
    return f"gate6-{campaign}"


def _info_hash(campaign: str) -> str:
    return hashlib.sha1(f"wos-v233-gate6:{campaign}".encode(), usedforsecurity=False).hexdigest()


def _small_content() -> bytes:
    pattern = b"world-of-seeds-v2-gate6-range-proof\n"
    return (pattern * (SMALL_FILE_SIZE // len(pattern) + 1))[:SMALL_FILE_SIZE]


def _write_large_file(path: Path) -> None:
    pattern = hashlib.sha256(b"world-of-seeds-v2-gate6-progressive-proof").digest() * 2048
    remaining = LARGE_FILE_SIZE
    with path.open("wb") as stream:
        while remaining:
            chunk = pattern[: min(len(pattern), remaining)]
            stream.write(chunk)
            remaining -= len(chunk)


def _manifest_entry(index: int) -> tuple[str, int]:
    if index == 0:
        return "gate6/small.bin", SMALL_FILE_SIZE
    if index == 1:
        return "gate6/large.bin", LARGE_FILE_SIZE
    return f"manifest/{index:05d}.bin", 0


def _update_manifest_digest(digest: Any, index: int, relative_path: str, size: int) -> None:
    digest.update(str(index).encode("ascii"))
    digest.update(b"\0")
    digest.update(relative_path.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(size).encode("ascii"))
    digest.update(b"\n")


async def _seed(campaign: str) -> dict[str, Any]:
    settings = get_settings()
    username = _username(campaign)
    torrent_name = _torrent_name(campaign)
    async with session_factory() as db:
        existing = await db.scalar(select(func.count(User.id)).where(User.username == username))
        if existing:
            raise RuntimeError("gate6 campaign user already exists")

        user = User(
            username=username,
            password_hash=DUMMY_PASSWORD_HASH,
            is_active=True,
            must_change_credentials=False,
        )
        torrent = ManagedTorrent(
            info_hash=_info_hash(campaign),
            name=torrent_name,
            total_size=SMALL_FILE_SIZE + LARGE_FILE_SIZE,
            state=ManagedTorrentState.READY,
            progress=1.0,
            ready_at=datetime.now(UTC),
            retention_expires_at=datetime.now(UTC) + timedelta(days=7),
            manifest_version=1,
            manifest_checksum="0" * 64,
            manifest_file_count=MANIFEST_FILE_COUNT,
            manifest_total_size=SMALL_FILE_SIZE + LARGE_FILE_SIZE,
        )
        request = TorrentRequest(
            user=user,
            managed_torrent=torrent,
            state=TorrentRequestState.READY,
            ready_at=datetime.now(UTC),
        )
        db.add_all([user, torrent, request])
        await db.flush()
        tokens = issue_session(db, user=user, settings=settings)
        user_id = user.id
        torrent_id = torrent.id
        request_id = request.id
        storage_key = torrent.storage_key
        await db.commit()

        digest = hashlib.sha256()
        first_file_id: uuid.UUID | None = None
        second_file_id: uuid.UUID | None = None
        for batch_start in range(0, MANIFEST_FILE_COUNT, BULK_BATCH_SIZE):
            rows: list[dict[str, Any]] = []
            batch_end = min(MANIFEST_FILE_COUNT, batch_start + BULK_BATCH_SIZE)
            for index in range(batch_start, batch_end):
                relative_path, size = _manifest_entry(index)
                file_id = uuid.uuid4()
                if index == 0:
                    first_file_id = file_id
                elif index == 1:
                    second_file_id = file_id
                _update_manifest_digest(digest, index, relative_path, size)
                rows.append(
                    {
                        "id": file_id,
                        "managed_torrent_id": torrent_id,
                        "file_index": index,
                        "relative_path": relative_path,
                        "size": size,
                    }
                )
            await db.execute(insert(TorrentFile), rows)
        torrent = await db.get(ManagedTorrent, torrent_id)
        if torrent is None:
            raise RuntimeError("gate6 torrent disappeared while seeding")
        torrent.manifest_checksum = digest.hexdigest()
        await db.commit()

    if first_file_id is None or second_file_id is None:
        raise RuntimeError("gate6 transfer files were not seeded")

    store = SharedContentStore(settings.data_root)
    store.prepare(storage_key)
    physical_root = settings.data_root / "content" / storage_key.hex / "gate6"
    physical_root.mkdir(mode=0o750)
    small_content = _small_content()
    (physical_root / "small.bin").write_bytes(small_content)
    _write_large_file(physical_root / "large.bin")

    allowed_host = next(
        host
        for host in settings.allowed_hosts
        if host not in {"127.0.0.1", "localhost", "test"}
    )
    return {
        "user_id": user_id,
        "request_id": request_id,
        "small_file_id": first_file_id,
        "large_file_id": second_file_id,
        "session_cookie_name": settings.session_cookie_name,
        "allowed_host": allowed_host,
        "token": tokens.session_token,
        "small_sha256": hashlib.sha256(small_content).hexdigest(),
    }


async def _lease_count(user_id: uuid.UUID) -> int:
    async with session_factory() as db:
        value = await db.scalar(
            select(func.count()).select_from(DownloadLease).where(DownloadLease.user_id == user_id)
        )
        await db.rollback()
    return int(value or 0)


async def _wait_for_no_leases(user_id: uuid.UUID, timeout: float = 10.0) -> int:
    deadline = time.monotonic() + timeout
    observed = await _lease_count(user_id)
    while observed and time.monotonic() < deadline:
        await asyncio.sleep(0.2)
        observed = await _lease_count(user_id)
    return observed


def _assert_status(response: httpx.Response, expected: int, label: str) -> None:
    if response.status_code != expected:
        raise RuntimeError(f"{label} returned HTTP {response.status_code}, expected {expected}")


def _exercise_manifest(client: httpx.Client, request_id: uuid.UUID) -> tuple[str, int]:
    url = f"/api/v2/torrents/{request_id}/download-manifest"
    snapshot: str | None = None
    seen = 0
    pages = 0
    for offset in range(0, MANIFEST_FILE_COUNT, MANIFEST_PAGE_SIZE):
        params: dict[str, str | int] = {"offset": offset, "limit": MANIFEST_PAGE_SIZE}
        if snapshot is not None:
            params["snapshot"] = snapshot
        response = client.get(url, params=params)
        _assert_status(response, 200, "manifest page")
        payload = response.json()
        if snapshot is None:
            candidate = payload.get("snapshot_id")
            if not isinstance(candidate, str) or len(candidate) != 64:
                raise RuntimeError("manifest snapshot id is invalid")
            snapshot = candidate
        elif payload.get("snapshot_id") != snapshot:
            raise RuntimeError("manifest snapshot changed during pagination")
        if payload.get("file_count") != MANIFEST_FILE_COUNT:
            raise RuntimeError("manifest file count changed during pagination")
        items = payload.get("items")
        if not isinstance(items, list) or len(items) != MANIFEST_PAGE_SIZE:
            raise RuntimeError("manifest page size is incomplete")
        indices = [item.get("file_index") for item in items if isinstance(item, dict)]
        if indices != list(range(offset, offset + MANIFEST_PAGE_SIZE)):
            raise RuntimeError("manifest page ordering or continuity failed")
        seen += len(items)
        pages += 1

    if snapshot is None or seen != MANIFEST_FILE_COUNT:
        raise RuntimeError("manifest pagination did not cover all entries")

    over_limit = client.get(url, params={"offset": 0, "limit": MANIFEST_PAGE_SIZE + 1})
    _assert_status(over_limit, 422, "manifest limit enforcement")
    stale = client.get(url, params={"snapshot": "0" * 64})
    _assert_status(stale, 409, "manifest snapshot enforcement")
    return snapshot, pages


def _exercise_small_transfer(
    client: httpx.Client,
    request_id: uuid.UUID,
    file_id: uuid.UUID,
    snapshot: str,
    expected_sha256: str,
) -> bool:
    url = f"/api/v2/torrents/{request_id}/files/{file_id}/download"
    head = client.head(url, params={"snapshot": snapshot})
    _assert_status(head, 200, "download HEAD")
    if head.headers.get("content-length") != str(SMALL_FILE_SIZE):
        raise RuntimeError("download HEAD length is incorrect")
    etag = head.headers.get("etag")
    if not etag:
        raise RuntimeError("download ETag is missing")

    full = client.get(url, params={"snapshot": snapshot})
    _assert_status(full, 200, "full file download")
    if hashlib.sha256(full.content).hexdigest() != expected_sha256:
        raise RuntimeError("full file integrity failed")

    midpoint = SMALL_FILE_SIZE // 2
    first = client.get(
        url,
        params={"snapshot": snapshot},
        headers={"Range": f"bytes=0-{midpoint - 1}", "If-Range": etag},
    )
    second = client.get(
        url,
        params={"snapshot": snapshot},
        headers={"Range": f"bytes={midpoint}-{SMALL_FILE_SIZE - 1}", "If-Range": etag},
    )
    _assert_status(first, 206, "first resumed range")
    _assert_status(second, 206, "second resumed range")
    if hashlib.sha256(first.content + second.content).hexdigest() != expected_sha256:
        raise RuntimeError("range resume integrity failed")

    invalid_range = client.get(
        url,
        params={"snapshot": snapshot},
        headers={"Range": f"bytes={SMALL_FILE_SIZE + 1}-{SMALL_FILE_SIZE + 2}"},
    )
    _assert_status(invalid_range, 416, "range limit enforcement")
    return True


def _exercise_progressive_cancel(
    client: httpx.Client,
    request_id: uuid.UUID,
    file_id: uuid.UUID,
    snapshot: str,
) -> bool:
    url = f"/api/v2/torrents/{request_id}/files/{file_id}/download"
    with client.stream("GET", url, params={"snapshot": snapshot}) as response:
        _assert_status(response, 200, "progressive large download")
        iterator = response.iter_raw(chunk_size=64 * 1024)
        first_chunk = next(iterator, b"")
        progressive = bool(first_chunk) and len(first_chunk) < LARGE_FILE_SIZE
        if not progressive:
            raise RuntimeError("large transfer did not start progressively")
        time.sleep(0.25)
        second_chunk = next(iterator, b"")
        if not second_chunk:
            raise RuntimeError("slow client did not receive a second transfer chunk")
        # Leaving the stream context without consuming the body is the cancellation/disconnect proof.
    return True


async def run_gate(campaign: str) -> dict[str, int | bool]:
    secret = await _seed(campaign)
    headers = {"Host": str(secret["allowed_host"])}
    cookies = {str(secret["session_cookie_name"]): str(secret["token"])}
    with httpx.Client(
        base_url="http://api:8000",
        headers=headers,
        cookies=cookies,
        timeout=30,
    ) as client:
        snapshot, pages = _exercise_manifest(client, secret["request_id"])
        pause_resume = _exercise_small_transfer(
            client,
            secret["request_id"],
            secret["small_file_id"],
            snapshot,
            str(secret["small_sha256"]),
        )
        progressive_cancel = _exercise_progressive_cancel(
            client,
            secret["request_id"],
            secret["large_file_id"],
            snapshot,
        )

    residual_leases = await _wait_for_no_leases(secret["user_id"])
    if residual_leases:
        raise RuntimeError(f"download cleanup left {residual_leases} residual lease(s)")
    return {
        "manifest_file_count": MANIFEST_FILE_COUNT,
        "manifest_pages": pages,
        "integrity_failures": 0,
        "residual_leases": residual_leases,
        "limit_violations": 0,
        "progressive_start": progressive_cancel,
        "pause_resume_cancel_verified": pause_resume and progressive_cancel,
        "secrets_or_business_identifiers_in_report": False,
    }


async def cleanup(campaign: str) -> None:
    settings = get_settings()
    username = _username(campaign)
    torrent_name = _torrent_name(campaign)
    storage_keys: list[uuid.UUID] = []
    async with session_factory() as db:
        storage_keys.extend(
            (
                await db.scalars(
                    select(ManagedTorrent.storage_key).where(ManagedTorrent.name == torrent_name)
                )
            ).all()
        )
        await db.execute(delete(ManagedTorrent).where(ManagedTorrent.name == torrent_name))
        await db.execute(delete(User).where(User.username == username))
        await db.commit()

    store = SharedContentStore(settings.data_root)
    for storage_key in storage_keys:
        try:
            store.purge(storage_key)
        except SharedContentStoreError as exc:
            raise RuntimeError("gate6 storage cleanup failed safely") from exc

    async with session_factory() as db:
        remaining_users = await db.scalar(
            select(func.count(User.id)).where(User.username == username)
        )
        remaining_torrents = await db.scalar(
            select(func.count(ManagedTorrent.id)).where(ManagedTorrent.name == torrent_name)
        )
        await db.rollback()
    if remaining_users or remaining_torrents:
        raise RuntimeError("gate6 cleanup left campaign database rows")


def main() -> int:
    args = parse_args()
    if args.mode == "cleanup":
        asyncio.run(cleanup(args.campaign))
        return 0
    result = asyncio.run(run_gate(args.campaign))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
