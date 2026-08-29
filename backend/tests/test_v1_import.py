import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentRequest,
    TorrentRequestState,
    User,
    V1ImportItem,
    V1ImportRun,
    V1ImportRunStatus,
)
from app.v1_import import (
    ImportDisposition,
    V1ImportConflictError,
    V1ImportError,
    apply_v1_import,
    load_inventory,
    plan_report,
    plan_v1_import,
    rollback_v1_import,
    write_private_report,
)

NOW = datetime(2026, 8, 28, 12, tzinfo=UTC)


def _row(
    username: str,
    info_hash: str,
    name: str = "release.mkv",
    *,
    record_id: uuid.UUID | None = None,
) -> dict[str, str]:
    return {
        "source_record_id": str(record_id or uuid.uuid4()),
        "username": username,
        "info_hash": info_hash,
        "name": name,
        "created_at": NOW.isoformat(),
    }


def _inventory(
    tmp_path: Path, rows: list[dict[str, str]], snapshot: str = "v1-snapshot-001"
) -> Path:
    path = tmp_path / f"{snapshot}.json"
    path.write_text(
        json.dumps({"schema": 1, "snapshot_id": snapshot, "rows": rows}),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _user(username: str, *, active: bool = True) -> User:
    return User(
        username=username,
        password_hash="not-used",
        is_active=active,
        created_at=NOW,
        updated_at=NOW,
    )


def test_inventory_is_strict_private_canonical_and_order_independent(tmp_path: Path) -> None:
    first_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    second_id = uuid.UUID("20000000-0000-4000-8000-000000000002")
    rows = [
        _row("Alice", "b" * 40, record_id=second_id),
        _row("Thomas", "a" * 40, record_id=first_id),
    ]
    first = load_inventory(_inventory(tmp_path, rows))
    second_path = tmp_path / "reordered.json"
    second_path.write_text(
        json.dumps({"schema": 1, "snapshot_id": "v1-snapshot-001", "rows": rows[::-1]}),
        encoding="utf-8",
    )
    second_path.chmod(0o600)

    assert first.fingerprint == load_inventory(second_path).fingerprint
    assert [row.source_record_id for row in first.rows] == [first_id, second_id]

    second_path.chmod(0o640)
    with pytest.raises(V1ImportError, match="group or other"):
        load_inventory(second_path)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda rows: rows.append(dict(rows[0])),
        lambda rows: rows[0].update({"info_hash": "A" * 40}),
        lambda rows: rows[0].update({"created_at": "2026-08-28T12:00:00"}),
        lambda rows: rows[0].update({"unexpected": "field"}),
        lambda rows: rows[0].update({"username": "invalid user"}),
    ),
)
def test_inventory_rejects_duplicates_noncanonical_hashes_and_schema_drift(
    tmp_path: Path, mutate: object
) -> None:
    rows = [_row("Thomas", "a" * 40)]
    mutate(rows)  # type: ignore[operator]
    with pytest.raises(V1ImportError):
        load_inventory(_inventory(tmp_path, rows))


@pytest.mark.asyncio
async def test_dry_run_plans_placeholders_without_writing(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    db_session.add(_user("Thomas"))
    await db_session.commit()
    inventory = load_inventory(_inventory(tmp_path, [_row("thomas", "a" * 40)]))

    plan = await plan_v1_import(db_session, inventory)

    assert plan.conflict_count == 0
    assert plan.actions[0].disposition is ImportDisposition.CREATE_PLACEHOLDER
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0
    assert await db_session.scalar(select(func.count()).select_from(V1ImportRun)) == 0
    report = plan_report(plan, mode="dry-run")
    rendered = json.dumps(report)
    assert "thomas" not in rendered
    assert "a" * 40 not in rendered
    assert "release.mkv" not in rendered


@pytest.mark.asyncio
async def test_inventory_export_rejects_non_postgresql_sources_before_connecting(
    tmp_path: Path,
) -> None:
    from app.v1_import import export_v1_inventory

    url = tmp_path / "source-url"
    url.write_text("sqlite+aiosqlite:///v1.db\n", encoding="utf-8")
    url.chmod(0o600)

    with pytest.raises(V1ImportError, match="PostgreSQL asyncpg"):
        await export_v1_inventory(url, tmp_path / "inventory.json", "v1-snapshot-001")


@pytest.mark.asyncio
async def test_apply_maps_shared_v1_ownership_once_and_replay_is_idempotent(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    db_session.add_all((_user("Thomas"), _user("Alice")))
    await db_session.commit()
    inventory = load_inventory(
        _inventory(
            tmp_path,
            [
                _row("thomas", "a" * 40, record_id=uuid.uuid4()),
                _row("ALICE", "a" * 40, record_id=uuid.uuid4()),
            ],
        )
    )

    result = await apply_v1_import(db_session, inventory, backup_id="rise2-backup-before-v1")
    await db_session.commit()

    torrents = list((await db_session.scalars(select(ManagedTorrent))).all())
    requests = list((await db_session.scalars(select(TorrentRequest))).all())
    assert len(torrents) == 1
    assert torrents[0].state is ManagedTorrentState.ERROR
    assert torrents[0].qb_state == "v1_import_reconcile_required"
    assert torrents[0].total_size == 0
    assert len(requests) == 2
    assert {request.state for request in requests} == {TorrentRequestState.REQUESTED}
    assert result.plan.create_torrent_count == 1
    assert result.plan.create_request_count == 2
    assert await db_session.scalar(select(func.count()).select_from(V1ImportItem)) == 2

    replay = await apply_v1_import(db_session, inventory, backup_id="rise2-backup-before-v1")
    await db_session.commit()
    assert replay.idempotent_replay is True
    assert replay.run_id == result.run_id
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 1
    assert await db_session.scalar(select(func.count()).select_from(TorrentRequest)) == 2


@pytest.mark.asyncio
async def test_idempotent_replay_detects_missing_imported_state(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    db_session.add(_user("Thomas"))
    await db_session.commit()
    inventory = load_inventory(_inventory(tmp_path, [_row("thomas", "a" * 40)]))
    await apply_v1_import(db_session, inventory, backup_id="rise2-backup-before-v1")
    await db_session.commit()
    request = await db_session.scalar(select(TorrentRequest))
    assert request is not None
    await db_session.delete(request)
    await db_session.commit()

    with pytest.raises(V1ImportError, match="no longer matches"):
        await apply_v1_import(db_session, inventory, backup_id="rise2-backup-before-v1")


@pytest.mark.asyncio
async def test_existing_managed_torrent_is_attached_without_being_rewritten(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    owner = _user("Thomas")
    torrent = ManagedTorrent(
        info_hash="a" * 40,
        name="release.mkv",
        total_size=123,
        state=ManagedTorrentState.READY,
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add_all((owner, torrent))
    await db_session.commit()
    inventory = load_inventory(_inventory(tmp_path, [_row("thomas", "a" * 40)]))

    result = await apply_v1_import(db_session, inventory, backup_id="rise2-backup-before-v1")
    await db_session.commit()

    assert result.plan.actions[0].disposition is ImportDisposition.ATTACH_EXISTING
    assert result.plan.create_torrent_count == 0
    assert torrent.total_size == 123
    assert torrent.state is ManagedTorrentState.READY


@pytest.mark.asyncio
async def test_conflicts_block_the_entire_apply_transaction(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    db_session.add(_user("Thomas"))
    await db_session.commit()
    inventory = load_inventory(
        _inventory(
            tmp_path,
            [_row("thomas", "a" * 40), _row("missing", "b" * 40)],
        )
    )

    with pytest.raises(V1ImportConflictError) as caught:
        await apply_v1_import(db_session, inventory, backup_id="rise2-backup-before-v1")
    await db_session.rollback()

    assert caught.value.plan.conflict_count == 1
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0
    assert await db_session.scalar(select(func.count()).select_from(V1ImportRun)) == 0


@pytest.mark.asyncio
async def test_rollback_deletes_only_rows_owned_by_the_import_run(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    db_session.add(_user("Thomas"))
    await db_session.commit()
    inventory = load_inventory(_inventory(tmp_path, [_row("thomas", "a" * 40)]))
    applied = await apply_v1_import(db_session, inventory, backup_id="rise2-backup-before-v1")
    await db_session.commit()

    report = await rollback_v1_import(db_session, applied.run_id)
    await db_session.commit()

    assert report["result"] == "rolled_back"
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 0
    assert await db_session.scalar(select(func.count()).select_from(TorrentRequest)) == 0
    run = await db_session.get(V1ImportRun, applied.run_id)
    assert run is not None and run.status is V1ImportRunStatus.ROLLED_BACK
    assert await db_session.scalar(select(func.count()).select_from(V1ImportItem)) == 1


@pytest.mark.asyncio
async def test_rollback_is_blocked_if_an_imported_request_changed(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    db_session.add(_user("Thomas"))
    await db_session.commit()
    inventory = load_inventory(_inventory(tmp_path, [_row("thomas", "a" * 40)]))
    applied = await apply_v1_import(db_session, inventory, backup_id="rise2-backup-before-v1")
    await db_session.commit()
    request = await db_session.scalar(select(TorrentRequest))
    assert request is not None
    request.state = TorrentRequestState.ACTIVE
    request.updated_at = NOW + timedelta(seconds=1)
    await db_session.commit()

    report = await rollback_v1_import(db_session, applied.run_id)
    await db_session.commit()

    assert report["result"] == "blocked"
    assert report["conflicts"][0]["code"] == "request_changed"
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 1


@pytest.mark.asyncio
async def test_rollback_is_blocked_if_placeholder_was_reconciled(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    db_session.add(_user("Thomas"))
    await db_session.commit()
    inventory = load_inventory(_inventory(tmp_path, [_row("thomas", "a" * 40)]))
    applied = await apply_v1_import(db_session, inventory, backup_id="rise2-backup-before-v1")
    await db_session.commit()
    torrent = await db_session.scalar(select(ManagedTorrent))
    assert torrent is not None
    torrent.total_size = 42
    torrent.updated_at = NOW + timedelta(seconds=1)
    await db_session.commit()

    report = await rollback_v1_import(db_session, applied.run_id)
    await db_session.commit()

    assert report["result"] == "blocked"
    assert any(item["code"] == "placeholder_changed" for item in report["conflicts"])
    assert await db_session.scalar(select(func.count()).select_from(ManagedTorrent)) == 1


@pytest.mark.asyncio
async def test_rollback_requires_reverse_import_run_order(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    db_session.add(_user("Thomas"))
    await db_session.commit()
    first_inventory = load_inventory(
        _inventory(tmp_path, [_row("thomas", "a" * 40)], snapshot="v1-snapshot-001")
    )
    first = await apply_v1_import(db_session, first_inventory, backup_id="rise2-backup-before-v1")
    await db_session.commit()
    second_inventory = load_inventory(
        _inventory(tmp_path, [_row("thomas", "a" * 40)], snapshot="v1-snapshot-002")
    )
    second = await apply_v1_import(
        db_session, second_inventory, backup_id="rise2-backup-before-v1-second"
    )
    await db_session.commit()

    blocked = await rollback_v1_import(db_session, first.run_id)
    assert blocked["conflicts"] == [{"code": "newer_import_run_exists"}]
    assert (await rollback_v1_import(db_session, second.run_id))["result"] == "rolled_back"
    await db_session.commit()
    assert (await rollback_v1_import(db_session, first.run_id))["result"] == "rolled_back"


def test_private_report_is_exclusive_and_mode_0600(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    write_private_report(target, {"result": "pass"})
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(V1ImportError, match="must not already exist"):
        write_private_report(target, {"result": "replacement"})
