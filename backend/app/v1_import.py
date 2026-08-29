from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth.security import CredentialValidationError, canonical_username
from app.models import (
    DownloadLease,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentJob,
    TorrentRequest,
    TorrentRequestState,
    TrackerActivity,
    User,
    V1ImportItem,
    V1ImportRun,
    V1ImportRunStatus,
)
from app.models.base import utc_now

INVENTORY_SCHEMA = 1
MAX_INVENTORY_ROWS = 100_000
INFO_HASH = re.compile(r"^[0-9a-f]{40}$")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
ACTIVE_REQUEST_STATES = {
    TorrentRequestState.REQUESTED,
    TorrentRequestState.ACTIVE,
    TorrentRequestState.READY,
}
IMPORT_NAMESPACE = uuid.UUID("2d771598-b89f-4d63-bbf0-0320355da1ef")


class V1ImportError(RuntimeError):
    """The optional V1 import cannot continue safely."""


class V1ImportConflictError(V1ImportError):
    def __init__(self, plan: V1ImportPlan) -> None:
        super().__init__("V1 import has unresolved conflicts; no target rows were written")
        self.plan = plan


class ImportDisposition(StrEnum):
    CREATE_PLACEHOLDER = "create_placeholder_and_request"
    ATTACH_EXISTING = "attach_existing_torrent"
    UNCHANGED = "already_mapped"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class V1InventoryRow:
    source_record_id: uuid.UUID
    username: str
    info_hash: str
    name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class V1Inventory:
    snapshot_id: str
    fingerprint: str
    rows: tuple[V1InventoryRow, ...]


@dataclass(frozen=True, slots=True)
class V1ImportAction:
    source_record_id: uuid.UUID
    disposition: ImportDisposition
    code: str
    target_user_id: uuid.UUID | None = None
    target_managed_torrent_id: uuid.UUID | None = None
    target_request_id: uuid.UUID | None = None
    create_managed_torrent: bool = False
    create_request: bool = False
    info_hash: str | None = None
    name: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class V1ImportPlan:
    snapshot_id: str
    fingerprint: str
    actions: tuple[V1ImportAction, ...]

    @property
    def conflict_count(self) -> int:
        return sum(action.disposition is ImportDisposition.CONFLICT for action in self.actions)

    @property
    def create_torrent_count(self) -> int:
        return sum(action.create_managed_torrent for action in self.actions)

    @property
    def create_request_count(self) -> int:
        return sum(action.create_request for action in self.actions)


@dataclass(frozen=True, slots=True)
class V1ImportApplyResult:
    run_id: uuid.UUID
    plan: V1ImportPlan
    idempotent_replay: bool


def _secure_regular_file(path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    except OSError as exc:
        raise V1ImportError(f"{label} cannot be read") from exc
    if path.is_symlink() or not resolved.is_file():
        raise V1ImportError(f"{label} must be a regular file without a symbolic link")
    if mode & 0o077:
        raise V1ImportError(f"{label} must not be accessible by group or other users")
    return resolved


def _canonical_payload(snapshot_id: str, rows: list[dict[str, str]]) -> bytes:
    return json.dumps(
        {"schema": INVENTORY_SCHEMA, "snapshot_id": snapshot_id, "rows": rows},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def load_inventory(path: Path) -> V1Inventory:
    source = _secure_regular_file(path, "V1 inventory")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V1ImportError("V1 inventory is not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema", "snapshot_id", "rows"}:
        raise V1ImportError("V1 inventory has unexpected top-level fields")
    if raw["schema"] != INVENTORY_SCHEMA:
        raise V1ImportError("V1 inventory schema is unsupported")
    snapshot_id = raw["snapshot_id"]
    if not isinstance(snapshot_id, str) or SAFE_IDENTIFIER.fullmatch(snapshot_id) is None:
        raise V1ImportError("V1 snapshot ID is invalid")
    raw_rows = raw["rows"]
    if not isinstance(raw_rows, list) or len(raw_rows) > MAX_INVENTORY_ROWS:
        raise V1ImportError("V1 inventory row count is invalid")

    rows: list[V1InventoryRow] = []
    canonical_rows: list[dict[str, str]] = []
    seen_ids: set[uuid.UUID] = set()
    seen_owner_hashes: set[tuple[str, str]] = set()
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict) or set(raw_row) != {
            "source_record_id",
            "username",
            "info_hash",
            "name",
            "created_at",
        }:
            raise V1ImportError("V1 inventory row fields are invalid")
        try:
            source_record_id = uuid.UUID(str(raw_row["source_record_id"]))
            created_at = datetime.fromisoformat(str(raw_row["created_at"]))
        except (ValueError, TypeError) as exc:
            raise V1ImportError("V1 inventory row identifier or timestamp is invalid") from exc
        username = raw_row["username"]
        info_hash = raw_row["info_hash"]
        name = raw_row["name"]
        if not isinstance(username, str) or not 1 <= len(username) <= 32 or "\x00" in username:
            raise V1ImportError("V1 inventory username is invalid")
        if not isinstance(info_hash, str) or INFO_HASH.fullmatch(info_hash) is None:
            raise V1ImportError("V1 inventory infohash is not canonical")
        if not isinstance(name, str) or not 1 <= len(name) <= 4096 or "\x00" in name:
            raise V1ImportError("V1 inventory torrent name is invalid")
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise V1ImportError("V1 inventory timestamps must include a UTC offset")
        created_at = created_at.astimezone(UTC)
        try:
            owner_hash = (canonical_username(username), info_hash)
        except CredentialValidationError as exc:
            raise V1ImportError("V1 inventory username cannot map to a V2 account") from exc
        if source_record_id in seen_ids or owner_hash in seen_owner_hashes:
            raise V1ImportError("V1 inventory contains duplicate ownership rows")
        seen_ids.add(source_record_id)
        seen_owner_hashes.add(owner_hash)
        row = V1InventoryRow(source_record_id, username, info_hash, name, created_at)
        rows.append(row)
        canonical_rows.append(
            {
                "source_record_id": str(source_record_id),
                "username": username,
                "info_hash": info_hash,
                "name": name,
                "created_at": created_at.isoformat(),
            }
        )
    ordered = sorted(
        zip(rows, canonical_rows, strict=True), key=lambda item: str(item[0].source_record_id)
    )
    rows = [item[0] for item in ordered]
    canonical_rows = [item[1] for item in ordered]
    fingerprint = hashlib.sha256(_canonical_payload(snapshot_id, canonical_rows)).hexdigest()
    return V1Inventory(snapshot_id, fingerprint, tuple(rows))


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    if target.exists():
        raise V1ImportError("output target must not already exist")
    with open(
        target,
        "x",
        encoding="utf-8",
        opener=lambda name, flags: os.open(name, flags, 0o600),
    ) as descriptor:
        json.dump(payload, descriptor, ensure_ascii=False, indent=2, sort_keys=True)
        descriptor.write("\n")


async def export_v1_inventory(
    source_url_file: Path,
    output: Path,
    snapshot_id: str,
) -> V1Inventory:
    """Read the legacy tables in a read-only transaction and create a private inventory."""
    if SAFE_IDENTIFIER.fullmatch(snapshot_id) is None:
        raise V1ImportError("V1 snapshot ID is invalid")
    url_file = _secure_regular_file(source_url_file, "V1 source URL file")
    try:
        source_url = url_file.read_text(encoding="utf-8").strip()
        parsed_url = make_url(source_url)
    except (OSError, UnicodeError, ValueError) as exc:
        raise V1ImportError("V1 source URL file is invalid") from exc
    if parsed_url.drivername != "postgresql+asyncpg" or not parsed_url.host:
        raise V1ImportError("V1 source must be a PostgreSQL asyncpg URL")
    try:
        engine = create_async_engine(source_url, pool_pre_ping=True)
    except Exception as exc:
        raise V1ImportError("V1 source connection could not be configured") from exc
    try:
        async with engine.connect() as connection, connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            result = await connection.execute(
                text(
                    "SELECT ut.id AS source_record_id, u.username, ut.info_hash, ut.name, "
                    "ut.created_at FROM user_torrents AS ut "
                    "JOIN users AS u ON u.id = ut.user_id "
                    "ORDER BY ut.id LIMIT :row_limit"
                ),
                {"row_limit": MAX_INVENTORY_ROWS + 1},
            )
            rows = [
                {
                    "source_record_id": str(row.source_record_id),
                    "username": str(row.username),
                    "info_hash": str(row.info_hash),
                    "name": str(row.name),
                    "created_at": row.created_at.isoformat(),
                }
                for row in result
            ]
    except Exception as exc:
        raise V1ImportError("read-only V1 inventory query failed") from exc
    finally:
        await engine.dispose()
    if len(rows) > MAX_INVENTORY_ROWS:
        raise V1ImportError("V1 inventory exceeds the bounded row limit")
    _write_private_json(
        output,
        {"schema": INVENTORY_SCHEMA, "snapshot_id": snapshot_id, "rows": rows},
    )
    return load_inventory(output)


async def plan_v1_import(session: AsyncSession, inventory: V1Inventory) -> V1ImportPlan:
    usernames = {canonical_username(row.username) for row in inventory.rows}
    hashes = {row.info_hash for row in inventory.rows}
    users = list(
        (
            await session.scalars(
                select(User).where(func.lower(User.username).in_(usernames)).order_by(User.id)
            )
        ).all()
    )
    users_by_name = {canonical_username(user.username): user for user in users}
    managed = list(
        (
            await session.scalars(
                select(ManagedTorrent).where(ManagedTorrent.info_hash.in_(hashes))
            )
        ).all()
    )
    managed_by_hash = {torrent.info_hash: torrent for torrent in managed}
    managed_ids = {torrent.id for torrent in managed}
    user_ids = {user.id for user in users}
    existing_requests = list(
        (
            await session.scalars(
                select(TorrentRequest).where(
                    TorrentRequest.user_id.in_(user_ids),
                    TorrentRequest.managed_torrent_id.in_(managed_ids),
                )
            )
        ).all()
    )
    requests_by_owner: dict[tuple[uuid.UUID, uuid.UUID], TorrentRequest | _PlannedRequest] = {
        (request.user_id, request.managed_torrent_id): request for request in existing_requests
    }
    planned: dict[str, ManagedTorrent | _PlannedTorrent] = dict(managed_by_hash)
    planned_names = {torrent.info_hash: torrent.name for torrent in managed}
    actions: list[V1ImportAction] = []

    for row in inventory.rows:
        user = users_by_name.get(canonical_username(row.username))
        if user is None:
            actions.append(_conflict(row, "target_user_missing"))
            continue
        if not user.is_active or user.deleted_at is not None:
            actions.append(_conflict(row, "target_user_inactive"))
            continue
        torrent = planned.get(row.info_hash)
        create_managed = torrent is None
        if torrent is not None and planned_names[row.info_hash] != row.name:
            actions.append(_conflict(row, "canonical_name_conflict"))
            continue
        if torrent is not None and torrent.state in {
            ManagedTorrentState.PURGING,
            ManagedTorrentState.PURGED,
        }:
            actions.append(_conflict(row, "target_torrent_terminal"))
            continue
        if torrent is None:
            managed_id = uuid.uuid5(
                IMPORT_NAMESPACE, f"{inventory.fingerprint}:torrent:{row.info_hash}"
            )
            planned[row.info_hash] = _PlannedTorrent(managed_id, row.name)
            planned_names[row.info_hash] = row.name
        else:
            managed_id = torrent.id
        existing_request = requests_by_owner.get((user.id, managed_id))
        if existing_request is not None:
            if existing_request.state not in ACTIVE_REQUEST_STATES:
                actions.append(_conflict(row, "target_request_history_conflict"))
                continue
            actions.append(
                V1ImportAction(
                    row.source_record_id,
                    ImportDisposition.UNCHANGED,
                    "active_request_already_exists",
                    target_user_id=user.id,
                    target_managed_torrent_id=managed_id,
                    target_request_id=existing_request.id,
                )
            )
            continue
        request_id = uuid.uuid5(
            IMPORT_NAMESPACE,
            f"{inventory.fingerprint}:request:{row.source_record_id}",
        )
        actions.append(
            V1ImportAction(
                row.source_record_id,
                ImportDisposition.CREATE_PLACEHOLDER
                if create_managed
                else ImportDisposition.ATTACH_EXISTING,
                "safe_placeholder_requires_reconciliation"
                if create_managed
                else "existing_managed_torrent_matched",
                target_user_id=user.id,
                target_managed_torrent_id=managed_id,
                target_request_id=request_id,
                create_managed_torrent=create_managed,
                create_request=True,
                info_hash=row.info_hash,
                name=row.name,
                created_at=row.created_at,
            )
        )
        requests_by_owner[(user.id, managed_id)] = _PlannedRequest(request_id)
    return V1ImportPlan(inventory.snapshot_id, inventory.fingerprint, tuple(actions))


@dataclass(frozen=True, slots=True)
class _PlannedTorrent:
    id: uuid.UUID
    name: str
    state: ManagedTorrentState = ManagedTorrentState.ERROR


@dataclass(frozen=True, slots=True)
class _PlannedRequest:
    id: uuid.UUID
    state: TorrentRequestState = TorrentRequestState.REQUESTED


def _conflict(row: V1InventoryRow, code: str) -> V1ImportAction:
    return V1ImportAction(row.source_record_id, ImportDisposition.CONFLICT, code)


async def _advisory_lock(session: AsyncSession) -> None:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, 0))"),
            {"lock_name": "world-of-seeds-v2-v1-import"},
        )


async def apply_v1_import(
    session: AsyncSession,
    inventory: V1Inventory,
    *,
    backup_id: str,
) -> V1ImportApplyResult:
    if SAFE_IDENTIFIER.fullmatch(backup_id) is None:
        raise V1ImportError("backup ID is invalid")
    await _advisory_lock(session)
    existing = await session.scalar(
        select(V1ImportRun)
        .where(V1ImportRun.source_fingerprint == inventory.fingerprint)
        .with_for_update()
    )
    if existing is not None:
        if existing.status is V1ImportRunStatus.ROLLED_BACK:
            raise V1ImportError("this exact inventory was already applied and rolled back")
        plan = await plan_v1_import(session, inventory)
        if plan.conflict_count or plan.create_request_count:
            raise V1ImportError("the previous import run no longer matches target state")
        return V1ImportApplyResult(existing.id, plan, True)

    plan = await plan_v1_import(session, inventory)
    if plan.conflict_count:
        raise V1ImportConflictError(plan)
    run_id = uuid.uuid4()
    run = V1ImportRun(
        id=run_id,
        source_fingerprint=inventory.fingerprint,
        source_snapshot_id=inventory.snapshot_id,
        backup_id=backup_id,
        source_rows=len(inventory.rows),
        created_torrents=plan.create_torrent_count,
        created_requests=plan.create_request_count,
    )
    session.add(run)
    for action in plan.actions:
        if not action.create_request:
            continue
        if (
            action.target_user_id is None
            or action.target_managed_torrent_id is None
            or action.target_request_id is None
            or action.info_hash is None
            or action.name is None
            or action.created_at is None
        ):
            raise V1ImportError("internal import plan is incomplete")
        if action.create_managed_torrent:
            session.add(
                ManagedTorrent(
                    id=action.target_managed_torrent_id,
                    storage_key=uuid.uuid4(),
                    info_hash=action.info_hash,
                    name=action.name,
                    total_size=0,
                    state=ManagedTorrentState.ERROR,
                    qb_state="v1_import_reconcile_required",
                    created_at=action.created_at,
                    updated_at=action.created_at,
                )
            )
        session.add(
            TorrentRequest(
                id=action.target_request_id,
                user_id=action.target_user_id,
                managed_torrent_id=action.target_managed_torrent_id,
                state=TorrentRequestState.REQUESTED,
                created_at=action.created_at,
                updated_at=action.created_at,
            )
        )
        session.add(
            V1ImportItem(
                run_id=run_id,
                source_record_id=action.source_record_id,
                target_user_id=action.target_user_id,
                target_managed_torrent_id=action.target_managed_torrent_id,
                target_request_id=action.target_request_id,
                managed_torrent_created=action.create_managed_torrent,
            )
        )
    await session.flush()
    return V1ImportApplyResult(run_id, plan, False)


async def rollback_v1_import(session: AsyncSession, run_id: uuid.UUID) -> dict[str, Any]:
    run = await session.get(V1ImportRun, run_id, with_for_update=True)
    if run is None:
        raise V1ImportError("V1 import run does not exist")
    if run.status is V1ImportRunStatus.ROLLED_BACK:
        return {"run_id": str(run_id), "result": "already_rolled_back", "conflicts": []}
    newer_run = await session.scalar(
        select(V1ImportRun.id)
        .where(
            V1ImportRun.status == V1ImportRunStatus.APPLIED,
            V1ImportRun.created_at > run.created_at,
        )
        .limit(1)
    )
    if newer_run is not None:
        return {
            "run_id": str(run_id),
            "result": "blocked",
            "conflicts": [{"code": "newer_import_run_exists"}],
        }
    items = list(
        (
            await session.scalars(
                select(V1ImportItem).where(V1ImportItem.run_id == run_id).order_by(V1ImportItem.id)
            )
        ).all()
    )
    conflicts: list[dict[str, str]] = []
    item_request_ids = {item.target_request_id for item in items}
    created_torrent_ids = {
        item.target_managed_torrent_id for item in items if item.managed_torrent_created
    }
    requests: dict[uuid.UUID, TorrentRequest] = {}
    for item in items:
        request = await session.get(TorrentRequest, item.target_request_id, with_for_update=True)
        if request is None:
            conflicts.append(
                {"source_record_id": str(item.source_record_id), "code": "request_missing"}
            )
            continue
        requests[request.id] = request
        if (
            request.state is not TorrentRequestState.REQUESTED
            or request.updated_at != request.created_at
        ):
            conflicts.append(
                {"source_record_id": str(item.source_record_id), "code": "request_changed"}
            )
        job_count = await session.scalar(
            select(func.count())
            .select_from(TorrentJob)
            .where(TorrentJob.torrent_request_id == request.id)
        )
        if job_count:
            conflicts.append(
                {"source_record_id": str(item.source_record_id), "code": "request_has_jobs"}
            )
    for torrent_id in created_torrent_ids:
        torrent = await session.get(ManagedTorrent, torrent_id, with_for_update=True)
        if torrent is None:
            conflicts.append(
                {"managed_torrent_ref": str(torrent_id), "code": "placeholder_missing"}
            )
            continue
        if (
            torrent.state is not ManagedTorrentState.ERROR
            or torrent.total_size != 0
            or torrent.qb_state != "v1_import_reconcile_required"
            or torrent.updated_at != torrent.created_at
        ):
            conflicts.append(
                {"managed_torrent_ref": str(torrent_id), "code": "placeholder_changed"}
            )
        related_request_ids = set(
            (
                await session.scalars(
                    select(TorrentRequest.id).where(TorrentRequest.managed_torrent_id == torrent_id)
                )
            ).all()
        )
        if not related_request_ids.issubset(item_request_ids):
            conflicts.append(
                {"managed_torrent_ref": str(torrent_id), "code": "torrent_has_other_requests"}
            )
        for model in (TorrentFile, TorrentJob, DownloadLease, TrackerActivity):
            count = await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.managed_torrent_id == torrent_id)
            )
            if count:
                conflicts.append(
                    {"managed_torrent_ref": str(torrent_id), "code": "torrent_has_runtime_state"}
                )
                break
    if conflicts:
        return {"run_id": str(run_id), "result": "blocked", "conflicts": conflicts}
    for request in requests.values():
        await session.delete(request)
    await session.flush()
    for torrent_id in created_torrent_ids:
        torrent = await session.get(ManagedTorrent, torrent_id, with_for_update=True)
        if torrent is not None:
            await session.delete(torrent)
    run.status = V1ImportRunStatus.ROLLED_BACK
    run.rolled_back_at = utc_now()
    await session.flush()
    return {
        "run_id": str(run_id),
        "result": "rolled_back",
        "deleted_requests": len(requests),
        "deleted_placeholders": len(created_torrent_ids),
        "conflicts": [],
    }


def plan_report(
    plan: V1ImportPlan, *, mode: str, run_id: uuid.UUID | None = None
) -> dict[str, Any]:
    counts = {disposition.value: 0 for disposition in ImportDisposition}
    for action in plan.actions:
        counts[action.disposition.value] += 1
    report: dict[str, Any] = {
        "schema": 1,
        "mode": mode,
        "snapshot_id": plan.snapshot_id,
        "source_fingerprint": plan.fingerprint,
        "source_rows": len(plan.actions),
        "counts": counts,
        "items": [
            {
                "source_record_id": str(action.source_record_id),
                "disposition": action.disposition.value,
                "code": action.code,
            }
            for action in plan.actions
        ],
        "contains_usernames": False,
        "contains_torrent_names": False,
        "contains_infohashes": False,
    }
    if run_id is not None:
        report["run_id"] = str(run_id)
    return report


def write_private_report(path: Path, report: dict[str, Any]) -> None:
    try:
        _write_private_json(path, report)
    except V1ImportError as exc:
        if "output target" in str(exc):
            raise V1ImportError("report target must not already exist") from exc
        raise
