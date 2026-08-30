import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal, Never

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from starlette.concurrency import run_in_threadpool

from app.admin import (
    RECOVER_CANCEL_REQUESTS_JOB,
    RECOVER_PURGE_METADATA_JOB,
    ReconciliationCursor,
    ReconciliationCursorError,
    ReconciliationRecoveryError,
    recovery_snapshot,
)
from app.auth.dependencies import (
    AppSettings,
    AuthContext,
    DbSession,
    require_admin_csrf,
    require_current_admin,
)
from app.integrations.observability_v2 import DEFAULT_STALE_AFTER
from app.models import (
    DatabaseOption,
    DatabaseOptionAudit,
    IntegrationServiceHealth,
    ManagedTorrent,
    ManagedTorrentState,
    QBittorrentInventoryItem,
    QBittorrentInventorySnapshot,
    SchedulerState,
    StorageLedger,
    StoragePressureState,
    TorrentJob,
    TorrentJobState,
    User,
    UserStorageUsage,
)
from app.options import (
    CATEGORY_LABELS,
    OPTION_SPECS,
    DatabaseOptionsDriftError,
    OptionsValidationError,
    PostgresOptionsRegistry,
)
from app.schemas.admin_v2 import (
    AdminV2OptionAudit,
    AdminV2OptionField,
    AdminV2OptionSection,
    AdminV2OptionsUpdate,
    AdminV2Overview,
    AdminV2ReconciliationAnomaly,
    AdminV2ReconciliationReport,
    AdminV2RecoveryRequest,
    AdminV2RecoveryResult,
    AdminV2SchedulerStatus,
    AdminV2StorageStatus,
)
from app.storage import SharedContentStore, SharedContentStoreError

router = APIRouter()


def _business_detail(code: str, message: str, field: str | None = None) -> dict[str, str | None]:
    return {"code": code, "message": message, "field": field}


def _raise_options_error(exc: Exception) -> Never:
    if isinstance(exc, OptionsValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_business_detail(exc.code, str(exc), exc.field),
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_business_detail(
            "admin_options_unavailable",
            "La configuration V2 est temporairement indisponible.",
        ),
    ) from exc


async def _overview(
    db: DbSession,
    *,
    service_controls_available: bool,
    changed_keys: tuple[str, ...] = (),
    restart_required: bool = False,
) -> AdminV2Overview:
    rows = {row.key: row for row in (await db.scalars(select(DatabaseOption))).all()}
    if len(rows) != len(OPTION_SPECS):
        raise DatabaseOptionsDriftError("database option registry is incomplete")
    sections: list[AdminV2OptionSection] = []
    for category, label in CATEGORY_LABELS.items():
        fields = [
            AdminV2OptionField(
                key=spec.key,
                label=spec.label,
                description=spec.description,
                input_type=spec.input_type,
                value=rows[spec.key].value,
                default=spec.default,
                unit=spec.unit,
                minimum=spec.minimum,
                maximum=spec.maximum,
                choices=list(spec.choices),
                editable=spec.editable,
                restart_required=spec.restart_required,
                version=rows[spec.key].version,
            )
            for spec in OPTION_SPECS
            if spec.category == category
        ]
        if fields:
            sections.append(AdminV2OptionSection(id=category, label=label, fields=fields))

    scheduler = await db.get(SchedulerState, 1)
    ledger = await db.get(StorageLedger, 1)
    logical_bytes = int(
        await db.scalar(select(func.coalesce(func.sum(UserStorageUsage.logical_bytes), 0))) or 0
    )
    actor = aliased(User)
    audit_rows = (
        await db.execute(
            select(DatabaseOptionAudit, actor.username)
            .outerjoin(actor, actor.id == DatabaseOptionAudit.actor_user_id)
            .order_by(DatabaseOptionAudit.changed_at.desc(), DatabaseOptionAudit.id.desc())
            .limit(50)
        )
    ).all()
    now = datetime.now(UTC)
    storage_options = {key: rows[key].value for key in rows if key.startswith("WOS_STORAGE_")}
    pressure = ledger.pressure if ledger is not None else StoragePressureState.NORMAL
    pressure_labels: dict[StoragePressureState, Literal["normal", "warning", "critical"]] = {
        StoragePressureState.NORMAL: "normal",
        StoragePressureState.WARNING: "warning",
        StoragePressureState.CRITICAL: "critical",
    }
    return AdminV2Overview(
        sections=sections,
        scheduler=AdminV2SchedulerStatus(
            desired_generation=scheduler.desired_generation if scheduler is not None else 0,
            applied_generation=scheduler.applied_generation if scheduler is not None else 0,
            synchronized=(
                scheduler is None or scheduler.desired_generation == scheduler.applied_generation
            ),
            rounds=scheduler.rounds if scheduler is not None else 0,
            lease_active=(
                scheduler is not None
                and scheduler.lease_expires_at is not None
                and scheduler.lease_expires_at.replace(tzinfo=UTC) > now
            ),
        ),
        storage=AdminV2StorageStatus(
            managed_bytes=ledger.managed_bytes if ledger is not None else 0,
            logical_bytes=logical_bytes,
            disk_total_bytes=ledger.disk_total_bytes if ledger is not None else 0,
            disk_free_bytes=ledger.disk_free_bytes if ledger is not None else 0,
            pressure=pressure_labels[pressure],
            managed_quota_bytes=int(storage_options["WOS_STORAGE_MANAGED_MAX_BYTES"]),
            user_quota_bytes=int(storage_options["WOS_STORAGE_USER_MAX_BYTES"]),
        ),
        audit=[
            AdminV2OptionAudit(
                key=event.option_key,
                version=event.version,
                old_value=event.old_value,
                new_value=event.new_value,
                actor=username,
                source=event.change_source,
                changed_at=event.changed_at,
            )
            for event, username in audit_rows
        ],
        changed_keys=list(changed_keys),
        restart_required=restart_required,
        service_controls_available=service_controls_available,
    )


@router.get("/overview", response_model=AdminV2Overview)
async def get_admin_overview(
    db: DbSession,
    settings: AppSettings,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> AdminV2Overview:
    try:
        return await _overview(
            db,
            service_controls_available=settings.runtime_profile == "v1",
        )
    except (DatabaseOptionsDriftError, KeyError, RuntimeError) as exc:
        _raise_options_error(exc)


@router.patch("/options", response_model=AdminV2Overview)
async def update_admin_options(
    payload: AdminV2OptionsUpdate,
    db: DbSession,
    settings: AppSettings,
    context: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> AdminV2Overview:
    try:
        result = await PostgresOptionsRegistry().update(
            db,
            payload.changes,
            actor_user_id=context.user.id,
        )
        await db.commit()
        return await _overview(
            db,
            service_controls_available=settings.runtime_profile == "v1",
            changed_keys=result.changed_keys,
            restart_required=result.restart_required,
        )
    except (DatabaseOptionsDriftError, OptionsValidationError, KeyError, RuntimeError) as exc:
        await db.rollback()
        _raise_options_error(exc)


@router.get("/reconciliation", response_model=AdminV2ReconciliationReport)
async def get_admin_reconciliation(
    db: DbSession,
    settings: AppSettings,
    _: Annotated[AuthContext, Depends(require_current_admin)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    cursor: Annotated[str | None, Query(max_length=4096)] = None,
) -> AdminV2ReconciliationReport:
    try:
        position = ReconciliationCursor.decode(cursor) if cursor else None
    except ReconciliationCursorError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "reconciliation_cursor_invalid"},
        ) from exc
    if position is None:
        ranked_snapshots = select(
            QBittorrentInventorySnapshot.id.label("id"),
            QBittorrentInventorySnapshot.account_ref.label("account_ref"),
            QBittorrentInventorySnapshot.observation_set.label("observation_set"),
            QBittorrentInventorySnapshot.truncated.label("truncated"),
            QBittorrentInventorySnapshot.checked_at.label("checked_at"),
            func.row_number()
            .over(
                partition_by=QBittorrentInventorySnapshot.account_ref,
                order_by=(
                    QBittorrentInventorySnapshot.checked_at.desc(),
                    QBittorrentInventorySnapshot.id.desc(),
                ),
            )
            .label("snapshot_rank"),
        ).subquery()
        latest = list(
            (
                await db.execute(
                    select(
                        ranked_snapshots.c.id,
                        ranked_snapshots.c.account_ref,
                        ranked_snapshots.c.observation_set,
                        ranked_snapshots.c.truncated,
                        ranked_snapshots.c.checked_at,
                    )
                    .where(ranked_snapshots.c.snapshot_rank == 1)
                    .order_by(ranked_snapshots.c.account_ref)
                )
            ).all()
        )
        health_rows = list(
            (
                await db.execute(
                    select(
                        IntegrationServiceHealth.account_ref,
                        IntegrationServiceHealth.observation_set,
                        IntegrationServiceHealth.account_count,
                    ).where(IntegrationServiceHealth.service == "qbittorrent")
                )
            ).all()
        )
        expected_accounts = {row.account_ref for row in health_rows}
        health_sets = {row.observation_set for row in health_rows}
        health_counts = {row.account_count for row in health_rows}
        expected_count = next(iter(health_counts)) if len(health_counts) == 1 else 0
        now = datetime.now(UTC)
        latest_accounts = {row.account_ref for row in latest}
        snapshot_sets = {row.observation_set for row in latest}
        complete = (
            expected_count > 0
            and len(expected_accounts) == expected_count
            and latest_accounts == expected_accounts
            and len(health_sets) == 1
            and snapshot_sets == health_sets
            and all(
                not row.truncated
                and now
                - (
                    row.checked_at
                    if row.checked_at.tzinfo is not None
                    else row.checked_at.replace(tzinfo=UTC)
                ).astimezone(UTC)
                <= DEFAULT_STALE_AFTER
                for row in latest
            )
        )
        snapshot_ids = [row.id for row in latest] if complete else []
        position = ReconciliationCursor("database", snapshot_ids=tuple(snapshot_ids))

    if position.snapshot_ids:
        retained_snapshots = int(
            await db.scalar(
                select(func.count())
                .select_from(QBittorrentInventorySnapshot)
                .where(QBittorrentInventorySnapshot.id.in_(position.snapshot_ids))
            )
            or 0
        )
        if retained_snapshots != len(position.snapshot_ids):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail={"code": "reconciliation_snapshot_expired"},
            )

    anomalies: list[AdminV2ReconciliationAnomaly] = []
    database_scanned = qbittorrent_scanned = storage_scanned = external_torrents = 0
    next_position: ReconciliationCursor | None = None
    if position.phase == "database":
        statement = select(ManagedTorrent).order_by(ManagedTorrent.id).limit(limit + 1)
        if position.after is not None:
            try:
                statement = statement.where(ManagedTorrent.id > uuid.UUID(position.after))
            except ValueError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={"code": "reconciliation_cursor_invalid"},
                ) from exc
        rows = list((await db.scalars(statement)).all())
        page = rows[:limit]
        database_scanned = len(page)
        snapshot_items = (
            list(
                (
                    await db.execute(
                        select(
                            QBittorrentInventoryItem,
                            QBittorrentInventorySnapshot.account_ref,
                        )
                        .join(
                            QBittorrentInventorySnapshot,
                            QBittorrentInventorySnapshot.id == QBittorrentInventoryItem.snapshot_id,
                        )
                        .where(
                            QBittorrentInventoryItem.snapshot_id.in_(position.snapshot_ids),
                            QBittorrentInventoryItem.info_hash.in_(
                                [item.info_hash for item in page]
                            ),
                        )
                    )
                ).all()
            )
            if page and position.snapshot_ids
            else []
        )
        by_identity = {(item.info_hash, account_ref): item for item, account_ref in snapshot_items}
        store = SharedContentStore(settings.data_root)
        for torrent in page:
            expects_physical = torrent.state not in {
                ManagedTorrentState.PENDING,
                ManagedTorrentState.PURGED,
            }
            try:
                storage_present = await run_in_threadpool(store.contains, torrent.storage_key)
            except SharedContentStoreError:
                storage_present = None
            qb_item = by_identity.get((torrent.info_hash, torrent.qbittorrent_account_ref))
            if expects_physical and position.snapshot_ids and qb_item is None:
                anomalies.append(
                    AdminV2ReconciliationAnomaly(
                        code="missing_qb_torrent",
                        severity="critical",
                        resource_id=str(torrent.id),
                        action="purge_metadata" if storage_present is False else "cancel_requests",
                    )
                )
            if qb_item is not None and qb_item.storage_key != torrent.storage_key:
                anomalies.append(
                    AdminV2ReconciliationAnomaly(
                        code="qb_identity_mismatch",
                        severity="critical",
                        resource_id=str(torrent.id),
                        action="manual_review",
                    )
                )
            if expects_physical and storage_present is False:
                anomalies.append(
                    AdminV2ReconciliationAnomaly(
                        code="missing_storage",
                        severity="critical",
                        resource_id=str(torrent.id),
                        action="purge_metadata" if qb_item is None else "cancel_requests",
                    )
                )
            if storage_present is None:
                anomalies.append(
                    AdminV2ReconciliationAnomaly(
                        code="storage_unavailable",
                        severity="critical",
                        resource_id=str(torrent.id),
                        action="inspect_storage",
                    )
                )
        if not position.snapshot_ids:
            anomalies.append(
                AdminV2ReconciliationAnomaly(
                    code="qbittorrent_unavailable",
                    severity="warning",
                    resource_id=None,
                    action="retry_inventory",
                )
            )
        if len(rows) > limit:
            next_position = ReconciliationCursor(
                "database", str(page[-1].id), position.snapshot_ids
            )
        else:
            next_position = ReconciliationCursor(
                "qbittorrent" if position.snapshot_ids else "storage",
                snapshot_ids=position.snapshot_ids,
            )
    elif position.phase == "qbittorrent":
        if position.snapshot_index >= len(position.snapshot_ids):
            next_position = ReconciliationCursor("storage", snapshot_ids=position.snapshot_ids)
        else:
            snapshot_id = position.snapshot_ids[position.snapshot_index]
            qb_statement = (
                select(QBittorrentInventoryItem)
                .where(QBittorrentInventoryItem.snapshot_id == snapshot_id)
                .order_by(QBittorrentInventoryItem.info_hash)
                .limit(limit + 1)
            )
            if position.after is not None:
                qb_statement = qb_statement.where(
                    QBittorrentInventoryItem.info_hash > position.after
                )
            qb_rows = list((await db.scalars(qb_statement)).all())
            qb_page = qb_rows[:limit]
            qbittorrent_scanned = len(qb_page)
            hashes = [item.info_hash for item in qb_page]
            known_identities = (
                {
                    (info_hash, account_ref)
                    for info_hash, account_ref in (
                        await db.execute(
                            select(
                                ManagedTorrent.info_hash,
                                ManagedTorrent.qbittorrent_account_ref,
                            ).where(ManagedTorrent.info_hash.in_(hashes))
                        )
                    ).all()
                }
                if hashes
                else set()
            )
            snapshot_account_ref = await db.scalar(
                select(QBittorrentInventorySnapshot.account_ref).where(
                    QBittorrentInventorySnapshot.id == snapshot_id
                )
            )
            if snapshot_account_ref is None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail={"code": "reconciliation_snapshot_expired"},
                )
            external_torrents = sum(not item.claims_wos_identity for item in qb_page)
            for item in qb_page:
                if (
                    item.claims_wos_identity
                    and (
                        item.info_hash,
                        snapshot_account_ref,
                    )
                    not in known_identities
                ):
                    anomalies.append(
                        AdminV2ReconciliationAnomaly(
                            code="orphan_wos_qb",
                            severity="warning",
                            resource_id=None,
                            action="manual_review",
                        )
                    )
            if external_torrents:
                anomalies.append(
                    AdminV2ReconciliationAnomaly(
                        code="external_torrents_read_only",
                        severity="info",
                        resource_id=None,
                        action="none",
                    )
                )
            if len(qb_rows) > limit:
                next_position = ReconciliationCursor(
                    "qbittorrent",
                    qb_page[-1].info_hash,
                    position.snapshot_ids,
                    position.snapshot_index,
                )
            elif position.snapshot_index + 1 < len(position.snapshot_ids):
                next_position = ReconciliationCursor(
                    "qbittorrent",
                    snapshot_ids=position.snapshot_ids,
                    snapshot_index=position.snapshot_index + 1,
                )
            else:
                next_position = ReconciliationCursor("storage", snapshot_ids=position.snapshot_ids)
    else:
        try:
            after = uuid.UUID(position.after) if position.after is not None else None
            inventory = await run_in_threadpool(
                SharedContentStore(settings.data_root).inventory_page,
                limit=limit,
                after=after,
            )
        except (SharedContentStoreError, ValueError) as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "storage_unavailable"},
            ) from exc
        storage_scanned = len(inventory.keys)
        known_storage = (
            set(
                (
                    await db.scalars(
                        select(ManagedTorrent.storage_key).where(
                            ManagedTorrent.storage_key.in_(inventory.keys)
                        )
                    )
                ).all()
            )
            if inventory.keys
            else set()
        )
        for key in inventory.keys:
            if key not in known_storage:
                anomalies.append(
                    AdminV2ReconciliationAnomaly(
                        code="orphan_storage",
                        severity="warning",
                        resource_id=str(key),
                        action="manual_review",
                    )
                )
        if inventory.invalid_entries:
            anomalies.append(
                AdminV2ReconciliationAnomaly(
                    code="unsafe_storage_entries",
                    severity="critical",
                    resource_id=None,
                    action="manual_review",
                )
            )
        if inventory.truncated:
            next_position = ReconciliationCursor(
                "storage", str(inventory.keys[-1]), position.snapshot_ids
            )

    return AdminV2ReconciliationReport(
        database_scanned=database_scanned,
        qbittorrent_scanned=qbittorrent_scanned,
        storage_scanned=storage_scanned,
        external_torrents=external_torrents,
        anomalies=anomalies,
        truncated=next_position is not None,
        next_cursor=next_position.encode() if next_position is not None else None,
    )


@router.post(
    "/reconciliation/{managed_torrent_id}/recover",
    response_model=AdminV2RecoveryResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def recover_admin_managed_torrent(
    managed_torrent_id: uuid.UUID,
    payload: AdminV2RecoveryRequest,
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> AdminV2RecoveryResult:
    try:
        expected = await recovery_snapshot(db, managed_torrent_id)
    except ReconciliationRecoveryError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": str(exc)},
        ) from exc
    job_type = (
        RECOVER_CANCEL_REQUESTS_JOB
        if payload.action == "cancel_requests"
        else RECOVER_PURGE_METADATA_JOB
    )
    key = f"recover:{payload.action}:{managed_torrent_id}:{expected.lifecycle_generation}"
    job = await db.scalar(select(TorrentJob).where(TorrentJob.idempotency_key == key))
    if job is None:
        job = TorrentJob(
            managed_torrent_id=managed_torrent_id,
            job_type=job_type,
            idempotency_key=key,
            state=TorrentJobState.QUEUED,
            max_attempts=3,
            available_at=datetime.now(UTC),
        )
        db.add(job)
        try:
            await db.commit()
            await db.refresh(job)
        except IntegrityError:
            await db.rollback()
            job = await db.scalar(select(TorrentJob).where(TorrentJob.idempotency_key == key))
            if job is None:
                raise
    return AdminV2RecoveryResult(
        recovery_id=str(job.id),
        managed_torrent_id=str(job.managed_torrent_id),
        state=job.state.value.lower(),
        action=payload.action,
        error_code=job.last_error_code,
    )


@router.get(
    "/reconciliation/recoveries/{recovery_id}",
    response_model=AdminV2RecoveryResult,
)
async def get_admin_recovery(
    recovery_id: uuid.UUID,
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> AdminV2RecoveryResult:
    job = await db.get(TorrentJob, recovery_id)
    if job is None or job.job_type not in {
        RECOVER_CANCEL_REQUESTS_JOB,
        RECOVER_PURGE_METADATA_JOB,
    }:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "recovery_not_found"})
    action: Literal["cancel_requests", "purge_metadata"] = (
        "cancel_requests" if job.job_type == RECOVER_CANCEL_REQUESTS_JOB else "purge_metadata"
    )
    return AdminV2RecoveryResult(
        recovery_id=str(job.id),
        managed_torrent_id=str(job.managed_torrent_id),
        state=job.state.value.lower(),
        action=action,
        error_code=job.last_error_code,
    )
