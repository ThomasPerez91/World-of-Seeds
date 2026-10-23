from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any, Literal, cast

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncSessionTransaction,
    async_sessionmaker,
    create_async_engine,
)
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope
from test_http_lease_lifecycle import seed_ready
from test_storage_admission_concurrency import postgres_storage as postgres_storage
from test_v2_file_downloads import CONTENT, _ready_file
from test_v2_folder_archives import _folder_torrent, _login
from test_v2_torrent_effects import INFO_HASH, FakeAdder, _router, _snapshot

from app.integrations.qbittorrent_v2 import QBittorrentV2TorrentSnapshot
from app.jobs.torrent_effects import TorrentEffectHandlers
from app.jobs.torrent_payloads import TorrentPayloadStore
from app.jobs.worker import TransientTorrentJobError
from app.models import (
    Base,
    DatabaseOption,
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.storage import SharedContentStore
from app.torrents import downloads
from app.torrents.downloads import (
    DownloadConcurrencyError,
    DownloadLeaseManager,
    DownloadRateLimiter,
    ManagedArchiveEntry,
    ManagedArchiveStreamingResponse,
    ManagedDownloadError,
    ManagedDownloadStreamingResponse,
    ManagedFileDownloader,
    ManagedFolderArchiver,
)
from app.torrents.traffic import DownloadTrafficScheduler


@dataclass
class Ticks:
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    ticks: asyncio.Queue[None] = field(default_factory=asyncio.Queue)
    intervals: list[float] = field(default_factory=list)

    async def wait(self, interval: float) -> None:
        self.intervals.append(interval)
        self.entered.set()
        await self.ticks.get()

    async def step(self) -> None:
        await asyncio.wait_for(self.entered.wait(), 2)
        self.entered.clear()
        self.now += timedelta(seconds=10)
        self.ticks.put_nowait(None)
        # Next sleep is reached only after the real independent SQL renew commits.
        await asyncio.wait_for(self.entered.wait(), 2)


@dataclass
class Domain:
    sessions: async_sessionmaker[AsyncSession]
    root: Path
    ids: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]
    storage_key: uuid.UUID
    ticks: Ticks = field(default_factory=Ticks)

    def manager(self, session: AsyncSession) -> DownloadLeaseManager:
        return DownloadLeaseManager(
            session, lease_seconds=30, clock=lambda: self.ticks.now, heartbeat_wait=self.ticks.wait
        )

    async def lease(
        self,
        manager: DownloadLeaseManager,
        *,
        owner: uuid.UUID | None = None,
        request: uuid.UUID | None = None,
        kind: Literal["file", "archive"] = "archive",
    ) -> DownloadLease:
        return await manager.acquire(
            user_id=owner or self.ids[0],
            managed_torrent_id=self.ids[1],
            torrent_request_id=request or self.ids[2],
            torrent_file_id=self.ids[3],
            max_concurrent=2,
            max_concurrent_global=8,
            kind=kind,
        )

    def archiver(self) -> ManagedFolderArchiver:
        return ManagedFolderArchiver(
            ManagedFileDownloader(SharedContentStore(self.root)),
            storage_key=self.storage_key,
            entries=(ManagedArchiveEntry("folder/seed.txt", len(CONTENT), 0),),
            manifest_checksum="e" * 64,
            manifest_version=1,
            download_name="example.zip",
            max_concurrent_global=1,
        )

    def response(
        self, manager: DownloadLeaseManager, lease: DownloadLease
    ) -> ManagedArchiveStreamingResponse:
        lease_id = lease.id

        async def admit() -> uuid.UUID:
            return lease_id

        return ManagedArchiveStreamingResponse(
            self.archiver(),
            chunk_size=1024,
            user_id=self.ids[0],
            admit=admit,
            leases=manager,
            limiter=DownloadRateLimiter(),
            per_user_bytes_per_second=0,
            global_bytes_per_second=0,
        )

    async def count(self) -> int:
        async with self.sessions() as session:
            return int(await session.scalar(select(func.count()).select_from(DownloadLease)) or 0)


class ObservedConcurrency(downloads._ManagedArchiveConcurrency):
    def __init__(self) -> None:
        super().__init__()
        self.waiting = asyncio.Event()

    async def acquire(self, limit: int) -> None:
        if self._active >= limit:
            self.waiting.set()
        await super().acquire(limit)


@pytest_asyncio.fixture
async def domain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Domain]:
    monkeypatch.setattr(downloads, "_managed_archive_concurrency", ObservedConcurrency())
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'leases.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        owner, torrent, request, file, _ = await _ready_file(session, tmp_path)
        result = Domain(
            sessions, tmp_path, (owner.id, torrent.id, request.id, file.id), torrent.storage_key
        )
    try:
        yield result
    finally:
        await engine.dispose()
        assert downloads._managed_archive_concurrency._active == 0
        assert not downloads._managed_archive_concurrency._waiters


@dataclass
class Connection:
    messages: list[Message] = field(default_factory=list)
    disconnect: asyncio.Event = field(default_factory=asyncio.Event)
    body_started: asyncio.Event = field(default_factory=asyncio.Event)
    allow_send: asyncio.Event = field(default_factory=asyncio.Event)
    block: bool = False

    async def receive(self) -> Message:
        await self.disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            self.body_started.set()
            if self.block:
                await self.allow_send.wait()
        self.messages.append(message)

    def run(
        self, response: ManagedArchiveStreamingResponse | ManagedDownloadStreamingResponse
    ) -> asyncio.Task[None]:
        return asyncio.create_task(
            response(
                cast(Scope, {"type": "http", "asgi": {"spec_version": "2.4"}}),
                self.receive,
                self.send,
            )
        )

    def zip(self) -> zipfile.ZipFile:
        body = b"".join(
            m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"
        )
        return zipfile.ZipFile(io.BytesIO(body))


async def queued() -> None:
    queue = cast(ObservedConcurrency, downloads._managed_archive_concurrency)
    await asyncio.wait_for(queue.waiting.wait(), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("steps", [1, 4, 16])
async def test_wait_below_and_above_ttl_then_valid_zip_without_resource_leaks(
    domain: Domain, steps: int
) -> None:
    await downloads._managed_archive_concurrency.acquire(1)
    async with domain.sessions() as session:
        manager = domain.manager(session)
        lease = await domain.lease(manager)
        lease_id = lease.id
        original_deadline = lease.expires_at
        connection = Connection()
        response = domain.response(manager, lease)
        task = connection.run(response)
        try:
            await queued()
            for _ in range(steps):
                await domain.ticks.step()
                async with domain.sessions() as check:
                    persisted = await check.get(DownloadLease, lease_id)
                    assert persisted is not None
                    assert persisted.expires_at.replace(tzinfo=UTC) > domain.ticks.now
                assert not connection.messages
            if steps > 3:
                assert domain.ticks.now > original_deadline
                # The real purge handler must still defer deletion after original TTL.
                async with domain.sessions() as change, change.begin():
                    torrent = await change.get(ManagedTorrent, domain.ids[1])
                    right = await change.get(TorrentRequest, domain.ids[2])
                    assert torrent is not None and right is not None
                    torrent.state, torrent.purge_after = (
                        ManagedTorrentState.PURGE_PENDING,
                        domain.ticks.now,
                    )
                    torrent.purge_stop_pending = False
                    right.state = TorrentRequestState.CANCELLED
                effects = TorrentEffectHandlers(
                    domain.sessions,
                    _router(
                        domain.sessions,
                        FakeAdder(),
                        QBittorrentV2TorrentSnapshot(INFO_HASH, "uploading", 1),
                    ),
                    TorrentPayloadStore(domain.root, allowed_tracker_hosts=["c411.org"]),
                    SharedContentStore(domain.root),
                    clock=lambda: domain.ticks.now,
                )
                with pytest.raises(TransientTorrentJobError) as error:
                    await effects.purge_torrent(
                        _snapshot(domain.ids[1], domain.ids[2], "PURGE_TORRENT")
                    )
                assert error.value.error_code == "torrent_download_active"
                assert (
                    domain.root / "content" / domain.storage_key.hex / "folder/seed.txt"
                ).read_bytes() == CONTENT
            await downloads._managed_archive_concurrency.release()
            await asyncio.wait_for(task, 3)
            with connection.zip() as archive:
                assert archive.testzip() is None
                assert archive.read("folder/seed.txt") == CONTENT
            assert await domain.count() == 0
            assert response._heartbeat is not None and response._heartbeat.done()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if downloads._managed_archive_concurrency._active:
                await downloads._managed_archive_concurrency.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cancel", "disconnect", "renewal_failure"])
async def test_queue_abort_never_sends_success_or_leaves_a_ticket(
    domain: Domain, mode: str
) -> None:
    await downloads._managed_archive_concurrency.acquire(1)
    async with domain.sessions() as session:
        manager = domain.manager(session)
        lease = await domain.lease(manager)
        connection = Connection()
        response = domain.response(manager, lease)
        task = connection.run(response)
        try:
            await queued()
            if mode == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif mode == "disconnect":
                connection.disconnect.set()
                await asyncio.wait_for(task, 2)
            else:
                async with domain.sessions() as revoke, revoke.begin():
                    owner = await revoke.get(User, domain.ids[0])
                    assert owner is not None
                    owner.is_active = False
                domain.ticks.ticks.put_nowait(None)
                with pytest.raises(ManagedDownloadError, match="lease was lost"):
                    await asyncio.wait_for(task, 2)
            assert not connection.messages
            assert await domain.count() == 0
            assert not downloads._managed_archive_concurrency._waiters
            assert response._heartbeat is not None and response._heartbeat.done()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await downloads._managed_archive_concurrency.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["complete", "disconnect", "cancel"])
async def test_heartbeat_survives_send_blocked_beyond_ttl(domain: Domain, mode: str) -> None:
    async with domain.sessions() as session:
        manager = domain.manager(session)
        lease = await domain.lease(manager)
        connection = Connection(block=True)
        response = domain.response(manager, lease)
        task = connection.run(response)
        try:
            await asyncio.wait_for(connection.body_started.wait(), 2)
            for _ in range(8):
                await domain.ticks.step()
            assert await domain.count() == 1
            if mode == "complete":
                connection.allow_send.set()
                await task
                with connection.zip() as archive:
                    assert archive.read("folder/seed.txt") == CONTENT
            elif mode == "disconnect":
                connection.disconnect.set()
                await asyncio.wait_for(task, 2)
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert response._heartbeat is not None and response._heartbeat.done()
            assert await domain.count() == 0
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["archive", "file"])
@pytest.mark.parametrize("cleanup_error", [False, True])
async def test_rollback_expired_orm_never_masks_initial_error(
    domain: Domain,
    monkeypatch: pytest.MonkeyPatch,
    kind: Literal["file", "archive"],
    cleanup_error: bool,
) -> None:
    async with domain.sessions() as session:
        manager = domain.manager(session)
        lease = await domain.lease(manager, kind=kind)
        lease_id = lease.id
        connection = Connection(block=True)
        if kind == "archive":
            response: ManagedArchiveStreamingResponse | ManagedDownloadStreamingResponse = (
                domain.response(manager, lease)
            )
        else:
            traffic = DownloadTrafficScheduler()
            await traffic.register(lease_id, domain.ids[0], len(CONTENT))
            opened = ManagedFileDownloader(SharedContentStore(domain.root)).open(
                domain.storage_key,
                "folder/seed.txt",
                expected_size=len(CONTENT),
                manifest_checksum="e" * 64,
                manifest_version=1,
                file_index=0,
            )
            response = ManagedDownloadStreamingResponse(
                opened,
                start=0,
                length=len(CONTENT),
                status_code=200,
                headers={},
                chunk_size=1024,
                user_id=domain.ids[0],
                lease=lease,
                leases=manager,
                limiter=DownloadRateLimiter(),
                traffic=traffic,
                per_user_bytes_per_second=0,
                global_bytes_per_second=0,
            )
        task = connection.run(response)
        try:
            await asyncio.wait_for(connection.body_started.wait(), 2)

            async def fail(_id: uuid.UUID) -> None:
                await session.rollback()
                session.expire(lease)
                raise ManagedDownloadError("initial renewal failure")

            monkeypatch.setattr(manager, "renew_independent", fail)
            release = manager.release
            if cleanup_error:

                async def release_then_fail(_id: uuid.UUID) -> None:
                    await release(_id)
                    raise RuntimeError("secondary cleanup failure")

                monkeypatch.setattr(manager, "release", release_then_fail)
            domain.ticks.ticks.put_nowait(None)
            expected = ManagedDownloadError if kind == "archive" else ExceptionGroup
            with pytest.raises(expected) as failure:
                await asyncio.wait_for(task, 3)
            primary = failure.value
            if isinstance(primary, ExceptionGroup):
                primary = primary.exceptions[0]
            assert "initial renewal failure" in str(primary)
            assert "MissingGreenlet" not in repr(failure.value)
            assert await domain.count() == 0
            if kind == "file":
                assert await traffic.snapshot() == (0, 0)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_eight_archive_leases_limit_ninth_and_users_keep_fifo_order(domain: Domain) -> None:
    owners: list[tuple[uuid.UUID, uuid.UUID]] = []
    async with domain.sessions() as session, session.begin():
        for index in range(4):
            owner = User(username=f"zip-owner-{index}", password_hash="unused")
            session.add(owner)
            await session.flush()
            right = TorrentRequest(
                user_id=owner.id,
                managed_torrent_id=domain.ids[1],
                state=TorrentRequestState.READY,
                unsubscribe_at=domain.ticks.now + timedelta(days=1),
            )
            session.add(right)
            await session.flush()
            owners.append((owner.id, right.id))
    async with AsyncExitStack() as stack:
        managers = [
            domain.manager(await stack.enter_async_context(domain.sessions())) for _ in range(9)
        ]
        leases = []
        for manager, (owner_id, right_id) in zip(
            managers, [pair for pair in owners for _ in range(2)], strict=False
        ):
            leases.append(await domain.lease(manager, owner=owner_id, request=right_id))
        with pytest.raises(DownloadConcurrencyError):
            await domain.lease(managers[-1])
        # Avoid renewing concurrently on SQLite's single writer; SQL concurrency
        # and admission fencing are exercised separately on PostgreSQL in CI.
        connections = [Connection(block=index == 0) for index in range(8)]
        responses = [
            domain.response(manager, lease)
            for manager, lease in zip(managers, leases, strict=False)
        ]
        tasks = [
            connection.run(response)
            for connection, response in zip(connections, responses, strict=True)
        ]
        try:
            await asyncio.wait_for(connections[0].body_started.wait(), 3)
            await queued()
            assert await domain.count() == 8
            assert not any(connection.messages for connection in connections[1:])
            connections[0].allow_send.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
            for connection in connections:
                with connection.zip() as archive:
                    assert archive.testzip() is None
                    assert archive.read("folder/seed.txt") == CONTENT
            assert await domain.count() == 0
            assert all(
                response._heartbeat is not None and response._heartbeat.done()
                for response in responses
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_lease_rechecked_before_success_headers(domain: Domain) -> None:
    async with domain.sessions() as session:
        manager = domain.manager(session)
        lease = await domain.lease(manager)
        response = domain.response(manager, lease)
        async with domain.sessions() as revoke, revoke.begin():
            owner = await revoke.get(User, domain.ids[0])
            assert owner is not None
            owner.is_active = False
        connection = Connection()
        with pytest.raises(ManagedDownloadError):
            await asyncio.wait_for(connection.run(response), 2)
        assert not connection.messages
        assert await domain.count() == 0
        assert response._heartbeat is not None and response._heartbeat.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["download-folder-archive", "download-archive"])
async def test_both_http_routes_keep_sql_lease_through_long_fifo_wait(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    queue = ObservedConcurrency()
    monkeypatch.setattr(downloads, "_managed_archive_concurrency", queue)
    request, _ = await _folder_torrent(db_session, data_root)
    option = await db_session.get(DatabaseOption, "WOS_FOLDER_ARCHIVE_MAX_CONCURRENT_GLOBAL")
    assert option is not None
    option.integer_value = 1
    await db_session.commit()
    await _login(client)
    listing = await client.get(f"/api/v2/torrents/{request.id}/download-directories")
    params = {"snapshot": listing.json()["snapshot_id"]}
    if route == "download-folder-archive":
        params["path"] = "Saison 1"
    ticks = Ticks()
    original_init = DownloadLeaseManager.__init__

    def init(self: DownloadLeaseManager, session: AsyncSession, **kwargs: Any) -> None:
        original_init(
            self, session, lease_seconds=30, clock=lambda: ticks.now, heartbeat_wait=ticks.wait
        )

    monkeypatch.setattr(DownloadLeaseManager, "__init__", init)
    await queue.acquire(1)
    task = asyncio.create_task(client.get(f"/api/v2/torrents/{request.id}/{route}", params=params))
    try:
        await queued()
        for _ in range(8):
            await ticks.step()
        assert not task.done()
        await queue.release()
        response = await asyncio.wait_for(task, 3)
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert archive.testzip() is None
            assert archive.namelist()
        assert await db_session.scalar(select(func.count()).select_from(DownloadLease)) == 0
        assert queue._active == 0 and not queue._waiters
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if queue._active:
            await queue.release()


@pytest.mark.asyncio
async def test_postgresql_nine_simultaneous_archive_admissions_keep_eight_leases(
    postgres_storage: tuple[async_sessionmaker[AsyncSession], str],
) -> None:
    sessions, prefix = postgres_storage
    ids = await seed_ready(sessions, prefix)
    now = datetime(2026, 10, 13, tzinfo=UTC)
    pairs: list[tuple[uuid.UUID, uuid.UUID]] = []
    async with sessions() as session, session.begin():
        for index in range(5):
            owner = User(username=f"{prefix}-zip-{index}", password_hash="unused")
            session.add(owner)
            await session.flush()
            right = TorrentRequest(
                user_id=owner.id,
                managed_torrent_id=ids[1],
                state=TorrentRequestState.READY,
                unsubscribe_at=now + timedelta(days=1),
            )
            session.add(right)
            await session.flush()
            pairs.append((owner.id, right.id))

    async def admit(index: int) -> uuid.UUID:
        owner_id, right_id = pairs[index // 2]
        async with sessions() as session:
            lease = await DownloadLeaseManager(
                session, lease_seconds=30, clock=lambda: now
            ).acquire(
                user_id=owner_id,
                managed_torrent_id=ids[1],
                torrent_request_id=right_id,
                torrent_file_id=ids[3],
                kind="archive",
                max_concurrent=2,
                max_concurrent_global=8,
            )
            return lease.id

    results = await asyncio.wait_for(
        asyncio.gather(*(admit(index) for index in range(9)), return_exceptions=True), 6
    )
    assert sum(isinstance(result, uuid.UUID) for result in results) == 8
    assert sum(isinstance(result, DownloadConcurrencyError) for result in results) == 1
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DownloadLease)
                .where(DownloadLease.managed_torrent_id == ids[1])
            )
            == 8
        )
        manager = DownloadLeaseManager(session, lease_seconds=30, clock=lambda: now)
        for result in results:
            if isinstance(result, uuid.UUID):
                await manager.release(result)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DownloadLease)
                .where(DownloadLease.managed_torrent_id == ids[1])
            )
            == 0
        )


@pytest.mark.asyncio
async def test_repeated_cancellation_still_finishes_sql_cleanup(
    domain: Domain, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with domain.sessions() as session:
        manager = domain.manager(session)
        lease = await domain.lease(manager)
        connection = Connection(block=True)
        response = domain.response(manager, lease)
        entered, allowed = asyncio.Event(), asyncio.Event()
        original = manager.release

        async def release(lease_id: uuid.UUID) -> None:
            entered.set()
            await allowed.wait()
            await original(lease_id)

        monkeypatch.setattr(manager, "release", release)
        task = connection.run(response)
        try:
            await asyncio.wait_for(connection.body_started.wait(), 2)
            task.cancel()
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            allowed.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert await domain.count() == 0
        finally:
            allowed.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_response_cancelled_before_start_has_no_sql_lease_or_heartbeat(
    domain: Domain,
) -> None:
    async with domain.sessions() as session:
        manager = domain.manager(session)

        async def admit() -> uuid.UUID:
            return (await domain.lease(manager)).id

        response = ManagedArchiveStreamingResponse(
            domain.archiver(),
            chunk_size=1024,
            user_id=domain.ids[0],
            admit=admit,
            leases=manager,
            limiter=DownloadRateLimiter(),
            per_user_bytes_per_second=0,
            global_bytes_per_second=0,
        )
        task = Connection().run(response)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await domain.count() == 0
        assert response._heartbeat is None


@pytest.mark.asyncio
async def test_cancel_after_sql_commit_explicitly_removes_admitted_lease(
    domain: Domain, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with domain.sessions() as session:
        original = AsyncSessionTransaction.__aexit__
        cancelled = False

        async def exit_transaction(transaction: AsyncSessionTransaction, *args: Any) -> None:
            nonlocal cancelled
            await original(transaction, *args)
            if transaction.session is session and not cancelled:
                cancelled = True
                raise asyncio.CancelledError

        monkeypatch.setattr(AsyncSessionTransaction, "__aexit__", exit_transaction)
        with pytest.raises(asyncio.CancelledError):
            await domain.lease(domain.manager(session))
        assert await domain.count() == 0


@pytest.mark.asyncio
async def test_cancel_drains_running_zip_reader_before_closing_generator_or_slot(
    domain: Domain, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with domain.sessions() as session:
        manager = domain.manager(session)
        lease = await domain.lease(manager)
        archiver = domain.archiver()
        started, allowed, closed = Event(), Event(), Event()
        original = archiver.iter_chunks

        def chunks(*, chunk_size: int) -> Iterator[bytes]:
            try:
                started.set()
                assert allowed.wait(3)
                yield from original(chunk_size=chunk_size)
            finally:
                closed.set()

        monkeypatch.setattr(archiver, "iter_chunks", chunks)
        lease_id = lease.id

        async def admit() -> uuid.UUID:
            return lease_id

        response = ManagedArchiveStreamingResponse(
            archiver,
            chunk_size=1024,
            user_id=domain.ids[0],
            admit=admit,
            leases=manager,
            limiter=DownloadRateLimiter(),
            per_user_bytes_per_second=0,
            global_bytes_per_second=0,
        )
        task = Connection().run(response)
        try:
            assert await asyncio.to_thread(started.wait, 2)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            assert downloads._managed_archive_concurrency._active == 1
            assert await domain.count() == 1
            allowed.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert closed.is_set()
            assert await domain.count() == 0
        finally:
            allowed.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2.0", "2.4"])
@pytest.mark.parametrize("phase", ["http.response.start", "http.response.body"])
async def test_send_disconnect_preserves_asgi_semantics_and_cleans_every_resource(
    domain: Domain, version: str, phase: str
) -> None:
    async with domain.sessions() as session:
        manager = domain.manager(session)
        lease = await domain.lease(manager)
        response = domain.response(manager, lease)
        connection = Connection()

        async def send(message: Message) -> None:
            if message["type"] == phase:
                raise OSError("client socket closed")
            await connection.send(message)

        scope = cast(Scope, {"type": "http", "asgi": {"spec_version": version}})
        if version == "2.4":
            with pytest.raises(ClientDisconnect) as error:
                await response(scope, connection.receive, send)
            assert isinstance(error.value.__cause__, OSError)
        else:
            with pytest.raises(OSError, match="client socket closed"):
                await response(scope, connection.receive, send)
        assert await domain.count() == 0
        assert response._heartbeat is not None and response._heartbeat.done()
