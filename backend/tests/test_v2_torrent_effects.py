import hashlib
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.coordination import RedisCoordinator
from app.integrations.account_routing import DeploymentAccountRouter, TorrentEffectRoute
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2ControlResult,
    QBittorrentV2DesiredControl,
    QBittorrentV2ManagedIdentity,
    QBittorrentV2TorrentSnapshot,
)
from app.jobs.torrent_effects import TorrentEffectHandlers, TorrentSyncEnqueuer
from app.jobs.torrent_payloads import TorrentPayloadStore, TorrentPayloadStoreError
from app.jobs.worker import TorrentJobSnapshot
from app.models import (
    Base,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentJob,
    TorrentRequest,
    TorrentRequestState,
    TrackerActivity,
    User,
)

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
        clock=lambda: NOW,
    )

    await effects.add_torrent(_snapshot(torrent_id, request_id, "ADD_TORRENT"))

    async with sessions() as session:
        torrent = await session.get(ManagedTorrent, torrent_id)
        request = await session.get(TorrentRequest, request_id)
        assert torrent is not None and torrent.state is ManagedTorrentState.DOWNLOADING
        assert torrent.qb_state == "added"
        assert torrent.tracker_account_ref == TRACKER_ACCOUNT_REF
        assert torrent.qbittorrent_account_ref == QBITTORRENT_ACCOUNT_REF
        assert request is not None and request.state is TorrentRequestState.ACTIVE
        activities = list((await session.scalars(select(TrackerActivity))).all())
        assert len(activities) == 1
        assert activities[0].tracker_account_ref == TRACKER_ACCOUNT_REF
    assert len(adder.contents) == 1
    assert b"private-user-passkey" not in adder.contents[0]
    with pytest.raises(TorrentPayloadStoreError):
        payloads.read(STORAGE_KEY)


@pytest.mark.asyncio
async def test_sync_handler_marks_completed_torrent_and_request_ready(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    torrent_id, request_id = await _create_domain(
        sessions,
        state=ManagedTorrentState.DOWNLOADING,
    )
    effects = TorrentEffectHandlers(
        sessions,
        _router(
            sessions,
            FakeAdder(),
            QBittorrentV2TorrentSnapshot(INFO_HASH, "stalledUP", 1.0),
        ),
        _payloads(tmp_path),
        clock=lambda: NOW,
    )

    await effects.sync_torrent(_snapshot(torrent_id, request_id, "SYNC_TORRENT"))

    async with sessions() as session:
        torrent = await session.get(ManagedTorrent, torrent_id)
        request = await session.get(TorrentRequest, request_id)
        assert torrent is not None and torrent.state is ManagedTorrentState.READY
        assert torrent.qb_state == "stalledup"
        assert request is not None and request.state is TorrentRequestState.READY
        assert request.ready_at is not None


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
