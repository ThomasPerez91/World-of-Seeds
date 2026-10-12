import asyncio
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_v2_torrent_api import RecordingRedis, login, prepare_user, torrent_content

from app.jobs.torrent_payloads import (
    OwnedTorrentPayload,
    TorrentPayloadStore,
    TorrentPayloadStoreError,
)
from app.main import app
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["write", "published", "flush"])
@pytest.mark.parametrize("reactivation", [False, True])
async def test_cancelled_upload_finishes_its_writer_and_cleans_only_its_attempt(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    reactivation: bool,
) -> None:
    await prepare_user(db_session)
    headers = await login(client)
    redis = RecordingRedis()
    app.state.redis_coordinator = redis
    baseline = 0
    if reactivation:
        response = await client.post(
            "/api/v2/torrents",
            files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
            headers=headers,
        )
        assert response.status_code == 201
        managed = await db_session.scalar(select(ManagedTorrent))
        job = await db_session.scalar(select(TorrentJob))
        assert managed is not None and job is not None
        managed.state = ManagedTorrentState.PURGED
        job.state = TorrentJobState.COMPLETED
        job.finished_at = datetime.now(UTC)
        await db_session.commit()
        baseline = 1
        redis.signals = 0
        redis.events.clear()
        redis.queue_events.clear()
    entered = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original_stage = TorrentPayloadStore.stage
    original_owned = TorrentPayloadStore.stage_owned
    original_flush = db_session.flush
    published = False
    writer_finished = threading.Event()

    def pause() -> None:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)

    def stage(self: TorrentPayloadStore, *args: Any, **kwargs: Any) -> Any:
        if phase == "write":
            pause()
        try:
            return original_stage(self, *args, **kwargs)
        finally:
            writer_finished.set()

    def stage_owned(self: TorrentPayloadStore, *args: Any, **kwargs: Any) -> OwnedTorrentPayload:
        nonlocal published
        owned = original_owned(self, *args, **kwargs)
        published = True
        if phase == "published":
            pause()
        return owned

    async def flush(*args: Any, **kwargs: Any) -> None:
        if phase == "flush" and published:
            entered.set()
            await asyncio.Event().wait()
        await original_flush(*args, **kwargs)

    monkeypatch.setattr(TorrentPayloadStore, "stage", stage)
    monkeypatch.setattr(TorrentPayloadStore, "stage_owned", stage_owned)
    monkeypatch.setattr(db_session, "flush", flush)
    task = asyncio.create_task(
        client.post(
            "/api/v2/torrents",
            files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
            headers=headers,
        )
    )
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        task.cancel()
        if phase != "flush":
            await asyncio.sleep(0)
            assert not task.done()
            # A second cancellation must not abandon the running writer either.
            task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(writer_finished.wait, 2)
        for model in (ManagedTorrent, TorrentRequest, TorrentJob):
            assert await db_session.scalar(select(func.count()).select_from(model)) == baseline
        if reactivation:
            assert (
                await db_session.scalar(select(ManagedTorrent.state)) is ManagedTorrentState.PURGED
            )
        assert list((data_root / "control" / "torrent-input").iterdir()) == []
        assert redis.signals == 0
        assert redis.events == []
        assert redis.queue_events == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


def test_cleanup_of_an_old_attempt_preserves_a_new_publication(tmp_path: Path) -> None:
    store = TorrentPayloadStore(tmp_path, allowed_tracker_hosts=["c411.org"])
    key = uuid.uuid4()
    old = store.stage_owned(torrent_content(), storage_key=key)
    new = store.stage_owned(torrent_content(), storage_key=key)
    try:
        old_fd = old.descriptor
        store.remove_owned(old)
        with pytest.raises(OSError):
            os.fstat(old_fd)
        assert store.read(key).info_hash == new.parsed.info_hash
        files = list((tmp_path / "control" / "torrent-input").iterdir())
        assert len(files) == 1
        assert b"old-user-passkey" not in files[0].read_bytes()
        store.remove_owned(new)
        assert list((tmp_path / "control" / "torrent-input").iterdir()) == []
    finally:
        old.close()
        new.close()


def test_failed_publication_cleans_private_and_published_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TorrentPayloadStore(tmp_path, allowed_tracker_hosts=["c411.org"])
    original = os.fsync
    calls = 0

    def fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:  # Final publication after the private file and its directory were synced.
            raise OSError("PRIVATE fsync error")
        original(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(TorrentPayloadStoreError):
        store.stage_owned(torrent_content(), storage_key=uuid.uuid4())
    assert list((tmp_path / "control" / "torrent-input").iterdir()) == []


@pytest.mark.asyncio
async def test_cancelled_commit_acknowledgement_keeps_the_durable_upload(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    from sqlalchemy import event

    await prepare_user(db_session)
    headers = await login(client)
    redis = RecordingRedis()
    app.state.redis_coordinator = redis
    commits = 0

    def after_commit(_session: object) -> None:
        nonlocal commits
        commits += 1
        if commits == 2:
            raise asyncio.CancelledError()

    event.listen(db_session.sync_session, "after_commit", after_commit)
    try:
        with pytest.raises(asyncio.CancelledError):
            await client.post(
                "/api/v2/torrents",
                files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
                headers=headers,
            )
    finally:
        event.remove(db_session.sync_session, "after_commit", after_commit)
    await db_session.close()
    assert await db_session.scalar(select(func.count()).select_from(TorrentJob)) == 1
    assert len(list((data_root / "control" / "torrent-input").iterdir())) == 1
    assert redis.signals == 0
    assert redis.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_precommit_failure_cleans_payload_without_masking_the_original_error(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_fails: bool,
) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    await prepare_user(db_session)
    headers = await login(client)
    original_owned = TorrentPayloadStore.stage_owned
    original_flush = db_session.flush
    published = False

    def stage_owned(self: TorrentPayloadStore, *args: Any, **kwargs: Any) -> OwnedTorrentPayload:
        nonlocal published
        result = original_owned(self, *args, **kwargs)
        published = True
        return result

    async def flush(*args: Any, **kwargs: Any) -> None:
        if published:
            raise SQLAlchemyError("PRIVATE SQL failure")
        await original_flush(*args, **kwargs)

    def cannot_clean(_store: TorrentPayloadStore, _owned: OwnedTorrentPayload) -> None:
        raise TorrentPayloadStoreError("PRIVATE cleanup failure")

    monkeypatch.setattr(TorrentPayloadStore, "stage_owned", stage_owned)
    monkeypatch.setattr(db_session, "flush", flush)
    if cleanup_fails:
        monkeypatch.setattr(TorrentPayloadStore, "remove_owned", cannot_clean)
    response = await client.post(
        "/api/v2/torrents",
        files={"torrent": ("film.torrent", torrent_content(), "application/x-bittorrent")},
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "torrent_request_unavailable"
    assert "PRIVATE" not in response.text
    assert len(list((data_root / "control" / "torrent-input").iterdir())) == int(cleanup_fails)
    assert await db_session.scalar(select(func.count()).select_from(TorrentJob)) == 0
