import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.integrations.account_routing import (
    AccountRoutingError,
    DeploymentAccountRouter,
    TorrentEffectRoute,
    parse_deployment_account_specs,
)
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2ControlResult,
    QBittorrentV2DesiredControl,
    QBittorrentV2ManagedIdentity,
    QBittorrentV2RunState,
    QBittorrentV2TorrentSnapshot,
)
from app.models import Base, ManagedTorrent

TRACKER_A = uuid.UUID("10000000-0000-0000-0000-000000000001")
TRACKER_B = uuid.UUID("10000000-0000-0000-0000-000000000002")
QB_A = uuid.UUID("20000000-0000-0000-0000-000000000001")
QB_B = uuid.UUID("20000000-0000-0000-0000-000000000002")


@pytest_asyncio.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'routing.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


class FakeAdder:
    async def add_torrent(
        self,
        _content: bytes,
        *,
        expected_info_hash: str,
        storage_key: uuid.UUID,
    ) -> object:
        return (expected_info_hash, storage_key)


class FakeQBittorrent:
    def __init__(self) -> None:
        self.control_calls: list[tuple[QBittorrentV2DesiredControl, ...]] = []

    async def remove_managed_torrent(self, _identity: QBittorrentV2ManagedIdentity) -> None:
        return None

    async def inspect_managed_torrents(
        self,
        identities: Sequence[QBittorrentV2ManagedIdentity],
    ) -> tuple[QBittorrentV2TorrentSnapshot, ...]:
        return tuple(
            QBittorrentV2TorrentSnapshot(identity.info_hash, "downloading", 0.5)
            for identity in identities
        )

    async def apply_managed_controls(
        self,
        controls: Sequence[QBittorrentV2DesiredControl],
    ) -> QBittorrentV2ControlResult:
        call = tuple(controls)
        self.control_calls.append(call)
        return QBittorrentV2ControlResult(
            tuple(control.info_hash for control in call),
            (),
            (),
            tuple(control.info_hash for control in call),
        )


def _route(
    tracker_ref: uuid.UUID,
    qb_ref: uuid.UUID,
    gateway: FakeQBittorrent | None = None,
) -> TorrentEffectRoute:
    return TorrentEffectRoute(tracker_ref, qb_ref, FakeAdder(), gateway or FakeQBittorrent())


def _deployment_json() -> SecretStr:
    return SecretStr(
        json.dumps(
            {
                "routes": [
                    {
                        "tracker_account_ref": str(TRACKER_A),
                        "qbittorrent_account_ref": str(QB_A),
                        "newgreedy_url": "http://newgreedy:8080",
                        "c411_passkey": "tracker-secret-123",
                        "qbittorrent_url": "http://qbittorrent-a:8080",
                        "qbittorrent_username": "worker-a",
                        "qbittorrent_password": "qb-secret-456",
                    },
                    {
                        "tracker_account_ref": str(TRACKER_B),
                        "qbittorrent_account_ref": str(QB_B),
                        "newgreedy_url": "http://newgreedy:8080",
                        "c411_passkey": "tracker-secret-789",
                        "qbittorrent_url": "http://qbittorrent-b:8080",
                        "qbittorrent_username": "worker-b",
                        "qbittorrent_password": "qb-secret-012",
                    },
                ]
            }
        )
    )


def test_deployment_registry_parses_opaque_routes_without_exposing_secrets() -> None:
    secret = _deployment_json()
    specs = parse_deployment_account_specs(secret)

    assert [(spec.tracker_account_ref, spec.qbittorrent_account_ref) for spec in specs] == [
        (TRACKER_A, QB_A),
        (TRACKER_B, QB_B),
    ]
    assert "tracker-secret" not in repr(specs)
    assert "qb-secret" not in repr(specs)
    assert "tracker-secret" not in repr(Settings(integration_accounts_json=secret))


@pytest.mark.parametrize(
    "payload",
    [
        "not-json-private-secret",
        json.dumps(
            {
                "routes": [
                    {
                        "tracker_account_ref": str(TRACKER_A),
                        "qbittorrent_account_ref": str(QB_A),
                        "newgreedy_url": "https://public.example:443",
                        "c411_passkey": "private-secret",
                        "qbittorrent_url": "http://qbittorrent:8080",
                        "qbittorrent_username": "worker",
                        "qbittorrent_password": "private-password",
                    }
                ]
            }
        ),
    ],
)
def test_invalid_deployment_registry_fails_with_only_safe_code(payload: str) -> None:
    with pytest.raises(AccountRoutingError) as caught:
        parse_deployment_account_specs(SecretStr(payload))

    assert str(caught.value) == "deployment_account_config_invalid"
    assert "private" not in str(caught.value)


@pytest.mark.asyncio
async def test_unassigned_torrent_gets_stable_route_independent_of_config_order(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    info_hash = "a" * 40
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(info_hash=info_hash, name="stable", total_size=1)
        session.add(torrent)
        await session.flush()
        torrent_id = torrent.id
    first = _route(TRACKER_A, QB_A)
    second = _route(TRACKER_B, QB_B)

    assigned = await DeploymentAccountRouter(sessions, (second, first)).resolve(
        torrent_id,
        info_hash,
    )
    replay = await DeploymentAccountRouter(sessions, (first, second)).resolve(
        torrent_id,
        info_hash,
    )

    assert (assigned.tracker_account_ref, assigned.qbittorrent_account_ref) == (
        replay.tracker_account_ref,
        replay.qbittorrent_account_ref,
    )
    async with sessions() as session:
        stored = await session.get(ManagedTorrent, torrent_id)
        assert stored is not None
        assert stored.tracker_account_ref == assigned.tracker_account_ref
        assert stored.qbittorrent_account_ref == assigned.qbittorrent_account_ref


@pytest.mark.asyncio
async def test_removed_assigned_route_fails_closed_without_reassignment(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as session, session.begin():
        torrent = ManagedTorrent(
            info_hash="b" * 40,
            name="removed",
            total_size=1,
            tracker_account_ref=TRACKER_B,
            qbittorrent_account_ref=QB_B,
        )
        session.add(torrent)
        await session.flush()
        torrent_id = torrent.id

    with pytest.raises(AccountRoutingError, match="managed_torrent_route_unavailable"):
        await DeploymentAccountRouter(sessions, (_route(TRACKER_A, QB_A),)).resolve(
            torrent_id,
            "b" * 40,
        )

    async with sessions() as session:
        stored = await session.get(ManagedTorrent, torrent_id)
        assert stored is not None
        assert stored.tracker_account_ref == TRACKER_B
        assert stored.qbittorrent_account_ref == QB_B


@pytest.mark.asyncio
async def test_scheduler_controls_are_grouped_by_opaque_qb_account(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    gateway_a = FakeQBittorrent()
    gateway_b = FakeQBittorrent()
    router = DeploymentAccountRouter(
        sessions,
        (
            _route(TRACKER_A, QB_A, gateway_a),
            _route(TRACKER_B, QB_B, gateway_b),
        ),
    )
    controls = tuple(
        QBittorrentV2DesiredControl(
            info_hash=character * 40,
            storage_key=uuid.uuid4(),
            run_state=QBittorrentV2RunState.RUNNING,
            download_limit_bytes_per_second=10,
            qbittorrent_account_ref=account_ref,
        )
        for character, account_ref in (("a", QB_A), ("b", QB_B))
    )

    result = await router.apply_managed_controls(controls)

    assert [[item.info_hash for item in call] for call in gateway_a.control_calls] == [["a" * 40]]
    assert [[item.info_hash for item in call] for call in gateway_b.control_calls] == [["b" * 40]]
    assert set(result.started) == {"a" * 40, "b" * 40}


@pytest.mark.asyncio
async def test_control_router_rejects_oversized_batch_before_any_account_effect(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeQBittorrent()
    router = DeploymentAccountRouter(sessions, (_route(TRACKER_A, QB_A, gateway),))
    controls = tuple(
        QBittorrentV2DesiredControl(
            info_hash=f"{index:040x}",
            storage_key=uuid.uuid4(),
            run_state=QBittorrentV2RunState.RUNNING,
            download_limit_bytes_per_second=0,
            qbittorrent_account_ref=QB_A,
        )
        for index in range(201)
    )

    with pytest.raises(AccountRoutingError, match="qbittorrent_control_set_too_large"):
        await router.apply_managed_controls(controls)

    assert gateway.control_calls == []


@pytest.mark.asyncio
async def test_postgresql_concurrent_assignment_converges_on_one_stable_route() -> None:
    database_url = os.environ.get("WOS_DATABASE_URL", "")
    if not database_url.startswith("postgresql+"):
        pytest.skip("PostgreSQL account routing test requires WOS_DATABASE_URL")
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    info_hash = "e" * 40
    try:
        async with factory() as session, session.begin():
            await session.execute(
                delete(ManagedTorrent).where(ManagedTorrent.info_hash == info_hash)
            )
            torrent = ManagedTorrent(info_hash=info_hash, name="concurrent-route", total_size=1)
            session.add(torrent)
            await session.flush()
            torrent_id = torrent.id
        first = _route(TRACKER_A, QB_A)
        second = _route(TRACKER_B, QB_B)
        routes = await asyncio.gather(
            DeploymentAccountRouter(factory, (first, second)).resolve(torrent_id, info_hash),
            DeploymentAccountRouter(factory, (second, first)).resolve(torrent_id, info_hash),
        )

        assert routes[0].tracker_account_ref == routes[1].tracker_account_ref
        assert routes[0].qbittorrent_account_ref == routes[1].qbittorrent_account_ref
    finally:
        async with factory() as session, session.begin():
            await session.execute(
                delete(ManagedTorrent).where(ManagedTorrent.info_hash == info_hash)
            )
        await engine.dispose()
