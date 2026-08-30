import hashlib
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.coordination import (
    RedisCoordinator,
    TorrentEventType,
    TorrentRealtimeEvent,
)
from app.integrations.account_routing import DeploymentAccountRouter, TorrentEffectRoute
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2ControlResult,
    QBittorrentV2DesiredControl,
    QBittorrentV2ManagedIdentity,
    QBittorrentV2MissingError,
    QBittorrentV2TorrentSnapshot,
)
from app.jobs.torrent_effects import TorrentEffectHandlers, TorrentSyncEnqueuer
from app.jobs.torrent_payloads import TorrentPayloadStore, TorrentPayloadStoreError
from app.jobs.worker import PermanentTorrentJobError, TorrentJobSnapshot, TransientTorrentJobError
from app.models import (
    Base,
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    StorageLedger,
    TorrentFile,
    TorrentJob,
    TorrentRequest,
    TorrentRequestState,
    TrackerActivity,
    User,
)
from app.storage import SharedContentStore

NOW = datetime(2026, 8, 21, 20, tzinfo=UTC)
STORAGE_KEY = uuid.UUID("12345678-1234-5678-1234-567812345678")
TRACKER_ACCOUNT_REF = uuid.UUID("11111111-1111-1111-1111-111111111111")
QBITTORRENT_ACCOUNT_REF = uuid.UUID("22222222-2222-2222-2222-222222222222")
INFO = {
    b"length": 5,
    b"name": b"Film.mkv",
    b"piece length": 16_384,
    b"pieces": b"p" * 20,
}
INFO_RAW = b"d6:lengthi5e4:name8:Film.mkv12:piece lengthi16384e6:pieces20:ppppppppppppppppppppe"
INFO_HASH = hashlib.sha1(INFO_RAW, usedforsecurity=False).hexdigest()


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


def _torrent() -> bytes:
    return _bencode(
        {
            b"announce": b"https://c411.org/user/private-user-passkey",
            b"announce-list": [[b"https://tk.c411.tw/user/private-user-passkey"]],
            b"info": INFO,
        }
    )


@pytest_asyncio.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'effects.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _payloads(tmp_path: Path) -> TorrentPayloadStore:
    root = tmp_path / "data"
    root.mkdir()
    return TorrentPayloadStore(root, allowed_tracker_hosts=["c411.org", "tk.c411.tw"])


def _content(tmp_path: Path) -> SharedContentStore:
    return SharedContentStore(tmp_path / "data")


async def _create_domain(
    sessions: async_sessionmaker[AsyncSession],
    *,
    state: ManagedTorrentState = ManagedTorrentState.PENDING,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with sessions() as session, session.begin():
        user = User(username=f"worker-{uuid.uuid4().hex[:8]}", password_hash="hash")
        torrent = ManagedTorrent(
            info_hash=INFO_HASH,
            storage_key=STORAGE_KEY,
            name="Film.mkv",
            total_size=5,
            state=state,
        )
        request = TorrentRequest(
            user=user,
            managed_torrent=torrent,
            state=TorrentRequestState.REQUESTED,
        )
        session.add(request)
        await session.flush()
        return torrent.id, request.id


def _snapshot(
    torrent_id: uuid.UUID,
    request_id: uuid.UUID | None,
    job_type: str,
) -> TorrentJobSnapshot:
    return TorrentJobSnapshot(
        id=uuid.uuid4(),
        managed_torrent_id=torrent_id,
        torrent_request_id=request_id,
        job_type=job_type,
        idempotency_key=f"test:{uuid.uuid4().hex}",
        attempt_count=1,
    )


class FakeAdder:
    def __init__(self) -> None:
        self.contents: list[bytes] = []

    async def add_torrent(
        self,
        content: bytes,
        *,
        expected_info_hash: str,
        storage_key: uuid.UUID,
    ) -> object:
        assert expected_info_hash == INFO_HASH
        assert storage_key == STORAGE_KEY
        self.contents.append(content)
        return object()


class FakeInspector:
    def __init__(self, snapshot: QBittorrentV2TorrentSnapshot) -> None:
        self.snapshot = snapshot
        self.removed: list[QBittorrentV2ManagedIdentity] = []

    async def remove_managed_torrent(self, identity: QBittorrentV2ManagedIdentity) -> None:
        self.removed.append(identity)

    async def inspect_managed_torrents(
        self,
        _identities: Sequence[QBittorrentV2ManagedIdentity],
    ) -> tuple[QBittorrentV2TorrentSnapshot, ...]:
        return (self.snapshot,)

    async def apply_managed_controls(
        self,
        _controls: Sequence[QBittorrentV2DesiredControl],
    ) -> QBittorrentV2ControlResult:
        return QBittorrentV2ControlResult((), (), (), ())


class MissingInspector(FakeInspector):
    async def inspect_managed_torrents(
        self,
        _identities: Sequence[QBittorrentV2ManagedIdentity],
    ) -> tuple[QBittorrentV2TorrentSnapshot, ...]:
        raise QBittorrentV2MissingError("qbittorrent_managed_torrent_missing")


def _missing_router(
    sessions: async_sessionmaker[AsyncSession],
    adder: FakeAdder,
) -> DeploymentAccountRouter:
    return DeploymentAccountRouter(
        sessions,
        (
            TorrentEffectRoute(
                TRACKER_ACCOUNT_REF,
                QBITTORRENT_ACCOUNT_REF,
                adder,
                MissingInspector(QBittorrentV2TorrentSnapshot(INFO_HASH, "missing", 0)),
            ),
        ),
    )


class RecordingRedis:
    def __init__(self) -> None:
        self.events: list[tuple[uuid.UUID, TorrentRealtimeEvent]] = []

    async def publish_torrent_event(
        self,
        user_id: uuid.UUID,
        event: TorrentRealtimeEvent,
    ) -> bool:
        self.events.append((user_id, event))
        return True


def _router(
    sessions: async_sessionmaker[AsyncSession],
    adder: FakeAdder,
    snapshot: QBittorrentV2TorrentSnapshot,
) -> DeploymentAccountRouter:
    return DeploymentAccountRouter(
        sessions,
        (
            TorrentEffectRoute(
                TRACKER_ACCOUNT_REF,
                QBITTORRENT_ACCOUNT_REF,
                adder,
                FakeInspector(snapshot),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_recovery_handler_collects_evidence_in_worker_and_purges_absent_content(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(sessions, state=ManagedTorrentState.READY)
    effects = TorrentEffectHandlers(
        sessions,
        _missing_router(sessions, FakeAdder()),
        _payloads(tmp_path),
        _content(tmp_path),
        clock=lambda: NOW,
    )

    await effects.recover_torrent(_snapshot(torrent_id, None, "RECOVER_PURGE_METADATA"))

    async with sessions() as session:
        torrent = await session.get(ManagedTorrent, torrent_id)
        request = await session.get(TorrentRequest, request_id)
        assert torrent is not None and torrent.state is ManagedTorrentState.PURGED
        assert request is not None and request.state is TorrentRequestState.CANCELLED


def test_payload_store_removes_user_passkeys_before_durable_write(tmp_path: Path) -> None:
    payloads = _payloads(tmp_path)
    parsed = payloads.stage(_torrent(), storage_key=STORAGE_KEY)

    staged = next((tmp_path / "data" / "control" / "torrent-input").iterdir())
    durable_content = staged.read_bytes()
    assert parsed.info_hash == INFO_HASH
    assert b"private-user-passkey" not in durable_content
    assert b"/announce" in durable_content
    assert payloads.read(STORAGE_KEY).content == durable_content


def test_payload_store_refuses_symlink_payload(tmp_path: Path) -> None:
    payloads = _payloads(tmp_path)
    payloads.stage(_torrent(), storage_key=STORAGE_KEY)
    path = tmp_path / "data" / "control" / "torrent-input" / f"{STORAGE_KEY.hex}.torrent"
    path.unlink()
    os.symlink(tmp_path / "outside", path)

    with pytest.raises(TorrentPayloadStoreError):
        payloads.read(STORAGE_KEY)


@pytest.mark.asyncio
async def test_add_handler_transitions_requests_and_removes_staged_payload(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(sessions)
    payloads = _payloads(tmp_path)
    payloads.stage(_torrent(), storage_key=STORAGE_KEY)
    adder = FakeAdder()
    effects = TorrentEffectHandlers(
        sessions,
        _router(
            sessions,
            adder,
            QBittorrentV2TorrentSnapshot(INFO_HASH, "downloading", 0.2),
        ),
        payloads,
        _content(tmp_path),
        clock=lambda: NOW,
    )

    await effects.add_torrent(_snapshot(torrent_id, request_id, "ADD_TORRENT"))

    async with sessions() as session:
        torrent = await session.get(ManagedTorrent, torrent_id)
        request = await session.get(TorrentRequest, request_id)
        assert torrent is not None and torrent.state is ManagedTorrentState.PAUSED
        assert torrent.qb_state == "stoppeddl"
        assert torrent.tracker_account_ref == TRACKER_ACCOUNT_REF
        assert torrent.qbittorrent_account_ref == QBITTORRENT_ACCOUNT_REF
        assert request is not None and request.state is TorrentRequestState.ACTIVE
        activities = list((await session.scalars(select(TrackerActivity))).all())
        files = list((await session.scalars(select(TorrentFile))).all())
        assert len(activities) == 1
        assert activities[0].tracker_account_ref == TRACKER_ACCOUNT_REF
        assert torrent.manifest_version == 1
        assert torrent.manifest_file_count == 1
        assert [(file.relative_path, file.size) for file in files] == [("Film.mkv", 5)]
    assert len(adder.contents) == 1
    assert (tmp_path / "data" / "content" / STORAGE_KEY.hex).is_dir()
    assert b"private-user-passkey" not in adder.contents[0]
    with pytest.raises(TorrentPayloadStoreError):
        payloads.read(STORAGE_KEY)


@pytest.mark.asyncio
async def test_add_handler_refuses_symlink_storage_before_qbittorrent(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(sessions)
    payloads = _payloads(tmp_path)
    payloads.stage(_torrent(), storage_key=STORAGE_KEY)
    content_root = tmp_path / "data" / "content"
    outside = tmp_path / "outside"
    content_root.mkdir()
    outside.mkdir()
    (content_root / STORAGE_KEY.hex).symlink_to(outside, target_is_directory=True)
    adder = FakeAdder()
    effects = TorrentEffectHandlers(
        sessions,
        _router(
            sessions,
            adder,
            QBittorrentV2TorrentSnapshot(INFO_HASH, "downloading", 0.2),
        ),
        payloads,
        _content(tmp_path),
        clock=lambda: NOW,
    )

    with pytest.raises(PermanentTorrentJobError) as failure:
        await effects.add_torrent(_snapshot(torrent_id, request_id, "ADD_TORRENT"))

    assert failure.value.error_code == "shared_storage_invalid"
    assert adder.contents == []
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_purge_handler_removes_content_manifest_and_accounting(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(
        sessions,
        state=ManagedTorrentState.READY,
    )
    payloads = _payloads(tmp_path)
    content = _content(tmp_path)
    content.prepare(STORAGE_KEY)
    managed_path = tmp_path / "data" / "content" / STORAGE_KEY.hex
    (managed_path / "Film.mkv").write_bytes(b"hello")
    async with sessions() as session, session.begin():
        torrent = await session.get(ManagedTorrent, torrent_id)
        request = await session.get(TorrentRequest, request_id)
        assert torrent is not None and request is not None
        torrent.state = ManagedTorrentState.PURGE_PENDING
        torrent.progress = 1
        torrent.purge_after = NOW - timedelta(hours=1)
        request.state = TorrentRequestState.CANCELLED
        session.add(
            TorrentFile(
                managed_torrent_id=torrent_id,
                file_index=0,
                relative_path="Film.mkv",
                size=5,
            )
        )
        session.add(StorageLedger(id=1, managed_bytes=5, disk_total_bytes=100, disk_free_bytes=50))
    adder = FakeAdder()
    effects = TorrentEffectHandlers(
        sessions,
        _router(
            sessions,
            adder,
            QBittorrentV2TorrentSnapshot(INFO_HASH, "uploading", 1),
        ),
        payloads,
        content,
        clock=lambda: NOW,
    )

    await effects.purge_torrent(_snapshot(torrent_id, request_id, "PURGE_TORRENT"))

    async with sessions() as session:
        torrent = await session.get(ManagedTorrent, torrent_id)
        ledger = await session.get(StorageLedger, 1)
        assert torrent is not None and torrent.state is ManagedTorrentState.PURGED
        assert torrent.purge_after is None
        assert torrent.manifest_version == 0
        assert ledger is not None and ledger.managed_bytes == 0
        assert await session.scalar(select(func.count()).select_from(TorrentFile)) == 0
    assert not managed_path.exists()


@pytest.mark.asyncio
async def test_purge_handler_waits_for_active_download_lease(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(
        sessions,
        state=ManagedTorrentState.READY,
    )
    payloads = _payloads(tmp_path)
    content = _content(tmp_path)
    content.prepare(STORAGE_KEY)
    managed_path = tmp_path / "data" / "content" / STORAGE_KEY.hex
    (managed_path / "Film.mkv").write_bytes(b"hello")
    async with sessions() as session, session.begin():
        torrent = await session.get(ManagedTorrent, torrent_id)
        request = await session.get(TorrentRequest, request_id)
        assert torrent is not None and request is not None
        torrent.state = ManagedTorrentState.PURGE_PENDING
        torrent.progress = 1
        torrent.purge_after = NOW - timedelta(hours=1)
        request.state = TorrentRequestState.CANCELLED
        file = TorrentFile(
            managed_torrent_id=torrent_id,
            file_index=0,
            relative_path="Film.mkv",
            size=5,
        )
        session.add(file)
        await session.flush()
        session.add(
            DownloadLease(
                user_id=request.user_id,
                managed_torrent_id=torrent_id,
                torrent_request_id=request_id,
                torrent_file_id=file.id,
                expires_at=NOW + timedelta(minutes=1),
            )
        )
    effects = TorrentEffectHandlers(
        sessions,
        _router(
            sessions,
            FakeAdder(),
            QBittorrentV2TorrentSnapshot(INFO_HASH, "uploading", 1),
        ),
        payloads,
        content,
        clock=lambda: NOW,
    )

    with pytest.raises(TransientTorrentJobError) as failure:
        await effects.purge_torrent(_snapshot(torrent_id, request_id, "PURGE_TORRENT"))

    assert failure.value.error_code == "torrent_download_active"
    assert managed_path.is_dir()


@pytest.mark.asyncio
async def test_sync_handler_marks_completed_torrent_and_request_ready(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(
        sessions,
        state=ManagedTorrentState.DOWNLOADING,
    )
    redis = RecordingRedis()
    effects = TorrentEffectHandlers(
        sessions,
        _router(
            sessions,
            FakeAdder(),
            QBittorrentV2TorrentSnapshot(INFO_HASH, "stalledUP", 1.0),
        ),
        _payloads(tmp_path),
        _content(tmp_path),
        redis=redis,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )

    await effects.sync_torrent(_snapshot(torrent_id, request_id, "SYNC_TORRENT"))

    async with sessions() as session:
        torrent = await session.get(ManagedTorrent, torrent_id)
        request = await session.get(TorrentRequest, request_id)
        assert torrent is not None and torrent.state is ManagedTorrentState.READY
        assert torrent.qb_state == "stalledup"
        assert torrent.progress == 1.0
        assert torrent.desired_active is False
        assert torrent.desired_priority is None
        assert torrent.desired_download_limit == 0
        assert request is not None and request.state is TorrentRequestState.READY
        assert request.ready_at is not None
        assert redis.events == [
            (
                request.user_id,
                TorrentRealtimeEvent(TorrentEventType.READY, request.id, NOW),
            )
        ]


@pytest.mark.asyncio
async def test_sync_handler_marks_missing_qb_torrent_as_permanent_error(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(
        sessions,
        state=ManagedTorrentState.DOWNLOADING,
    )
    route = TorrentEffectRoute(
        TRACKER_ACCOUNT_REF,
        QBITTORRENT_ACCOUNT_REF,
        FakeAdder(),
        MissingInspector(QBittorrentV2TorrentSnapshot(INFO_HASH, "missing", 0)),
    )
    effects = TorrentEffectHandlers(
        sessions,
        DeploymentAccountRouter(sessions, (route,)),
        _payloads(tmp_path),
        _content(tmp_path),
        clock=lambda: NOW,
    )

    with pytest.raises(PermanentTorrentJobError) as failure:
        await effects.sync_torrent(_snapshot(torrent_id, request_id, "SYNC_TORRENT"))

    assert failure.value.error_code == "qbittorrent_managed_torrent_missing"
    assert failure.value.torrent_state is ManagedTorrentState.ERROR


@pytest.mark.asyncio
async def test_sync_handler_persists_stall_backoff_and_resets_on_real_progress(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(
        sessions,
        state=ManagedTorrentState.DOWNLOADING,
    )
    async with sessions() as session, session.begin():
        torrent = await session.get(ManagedTorrent, torrent_id)
        assert torrent is not None
        torrent.desired_active = True
        torrent.desired_priority = 0
    payloads = _payloads(tmp_path)
    content = _content(tmp_path)

    async def sync(at: datetime, *, progress: float, downloaded: int) -> ManagedTorrent:
        effects = TorrentEffectHandlers(
            sessions,
            _router(
                sessions,
                FakeAdder(),
                QBittorrentV2TorrentSnapshot(
                    INFO_HASH,
                    "stalledDL",
                    progress,
                    downloaded,
                ),
            ),
            payloads,
            content,
            clock=lambda: at,
        )
        await effects.sync_torrent(_snapshot(torrent_id, request_id, "SYNC_TORRENT"))
        async with sessions() as session:
            stored = await session.get(ManagedTorrent, torrent_id)
            assert stored is not None
            session.expunge(stored)
            return stored

    baseline = await sync(NOW, progress=0.99, downloaded=990)
    assert baseline.state is ManagedTorrentState.DOWNLOADING
    assert baseline.last_progress_at == NOW.replace(tzinfo=None)

    first_stall = await sync(NOW + timedelta(seconds=61), progress=0.99, downloaded=990)
    assert first_stall.state is ManagedTorrentState.PAUSED
    assert first_stall.stall_count == 1
    assert first_stall.scheduler_retry_at == (NOW + timedelta(minutes=3, seconds=61)).replace(
        tzinfo=None
    )
    assert first_stall.desired_active is True

    same_cooldown = await sync(NOW + timedelta(minutes=2), progress=0.99, downloaded=990)
    assert same_cooldown.state is ManagedTorrentState.PAUSED
    assert same_cooldown.stall_count == 1
    assert same_cooldown.scheduler_retry_at == first_stall.scheduler_retry_at

    resumed = await sync(NOW + timedelta(minutes=4, seconds=2), progress=0.99, downloaded=990)
    assert resumed.state is ManagedTorrentState.DOWNLOADING
    assert resumed.stall_count == 1
    assert resumed.scheduler_retry_at is None

    second_stall = await sync(NOW + timedelta(minutes=5, seconds=3), progress=0.99, downloaded=990)
    assert second_stall.state is ManagedTorrentState.PAUSED
    assert second_stall.stall_count == 2
    assert second_stall.scheduler_retry_at == (NOW + timedelta(minutes=10, seconds=3)).replace(
        tzinfo=None
    )

    third_resume = await sync(NOW + timedelta(minutes=10, seconds=4), progress=0.99, downloaded=990)
    assert third_resume.state is ManagedTorrentState.DOWNLOADING
    assert third_resume.stall_count == 2

    third_stall = await sync(NOW + timedelta(minutes=11, seconds=5), progress=0.99, downloaded=990)
    assert third_stall.state is ManagedTorrentState.PAUSED
    assert third_stall.stall_count == 3
    assert third_stall.scheduler_retry_at == (NOW + timedelta(minutes=21, seconds=5)).replace(
        tzinfo=None
    )

    recovered = await sync(NOW + timedelta(minutes=12), progress=0.9901, downloaded=991)
    assert recovered.state is ManagedTorrentState.DOWNLOADING
    assert recovered.stall_count == 0
    assert recovered.scheduler_retry_at is None
    assert recovered.last_downloaded_bytes == 991


@pytest.mark.asyncio
async def test_scheduler_resume_starts_a_fresh_stall_window(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(sessions, state=ManagedTorrentState.PAUSED)
    async with sessions() as session, session.begin():
        torrent = await session.get(ManagedTorrent, torrent_id)
        assert torrent is not None
        torrent.qb_state = "stoppedDL"
        torrent.last_progress_at = (NOW - timedelta(hours=1)).replace(tzinfo=None)
        torrent.last_downloaded_bytes = 0
    effects = TorrentEffectHandlers(
        sessions,
        _router(
            sessions,
            FakeAdder(),
            QBittorrentV2TorrentSnapshot(INFO_HASH, "downloading", 0, 0),
        ),
        _payloads(tmp_path),
        _content(tmp_path),
        clock=lambda: NOW,
    )

    await effects.sync_torrent(_snapshot(torrent_id, request_id, "SYNC_TORRENT"))

    async with sessions() as session:
        torrent = await session.get(ManagedTorrent, torrent_id)
    assert torrent is not None
    assert torrent.state is ManagedTorrentState.DOWNLOADING
    assert torrent.last_progress_at == NOW.replace(tzinfo=None)
    assert torrent.scheduler_retry_at is None


@pytest.mark.asyncio
async def test_one_new_byte_keeps_a_very_slow_torrent_healthy(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(
        sessions,
        state=ManagedTorrentState.DOWNLOADING,
    )
    payloads = _payloads(tmp_path)
    content = _content(tmp_path)

    async def sync(at: datetime, downloaded: int) -> None:
        effects = TorrentEffectHandlers(
            sessions,
            _router(
                sessions,
                FakeAdder(),
                QBittorrentV2TorrentSnapshot(INFO_HASH, "downloading", 0.5, downloaded),
            ),
            payloads,
            content,
            clock=lambda: at,
        )
        await effects.sync_torrent(_snapshot(torrent_id, request_id, "SYNC_TORRENT"))

    await sync(NOW, 500)
    await sync(NOW + timedelta(seconds=61), 501)

    async with sessions() as session:
        torrent = await session.get(ManagedTorrent, torrent_id)
    assert torrent is not None
    assert torrent.state is ManagedTorrentState.DOWNLOADING
    assert torrent.stall_count == 0
    assert torrent.last_downloaded_bytes == 501
    assert torrent.last_progress_at == (NOW + timedelta(seconds=61)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_periodic_enqueuer_coalesces_one_active_sync_job_per_torrent(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _create_domain(sessions, state=ManagedTorrentState.DOWNLOADING)
    enqueuer = TorrentSyncEnqueuer(
        sessions,
        RedisCoordinator.unconfigured(),
        clock=lambda: NOW,
    )

    assert await enqueuer.enqueue_once() == (5, 1)
    assert await enqueuer.enqueue_once() == (5, 0)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TorrentJob)) == 1
