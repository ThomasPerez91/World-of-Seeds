import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal, Never

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import aliased
from starlette.concurrency import run_in_threadpool

from app.admin import (
    ReconciliationRecoveryError,
    reconcile_inventory,
    recover_orphaned_torrent,
    recovery_snapshot,
)
from app.auth.dependencies import (
    AppSettings,
    AuthContext,
    DbSession,
    require_admin_csrf,
    require_current_admin,
)
from app.core.database import session_factory
from app.integrations.account_routing import build_deployment_account_router
from app.integrations.http import IntegrationRequestError
from app.integrations.qbittorrent_v2 import (
    QBittorrentV2Gateway,
    QBittorrentV2Inventory,
    QBittorrentV2ManagedIdentity,
    QBittorrentV2MissingError,
)
from app.jobs.torrent_payloads import MAX_MANAGED_TORRENT_BYTES
from app.models import (
    DatabaseOption,
    DatabaseOptionAudit,
    ManagedTorrent,
    SchedulerState,
    StorageLedger,
    StoragePressureState,
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
    )


@router.get("/overview", response_model=AdminV2Overview)
async def get_admin_overview(
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> AdminV2Overview:
    try:
        return await _overview(db)
    except (DatabaseOptionsDriftError, KeyError, RuntimeError) as exc:
        _raise_options_error(exc)


@router.patch("/options", response_model=AdminV2Overview)
async def update_admin_options(
    payload: AdminV2OptionsUpdate,
    db: DbSession,
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
) -> AdminV2ReconciliationReport:
    rows = list(
        (
            await db.scalars(select(ManagedTorrent).order_by(ManagedTorrent.id).limit(limit + 1))
        ).all()
    )
    database_truncated = len(rows) > limit
    torrents = tuple(rows[:limit])
    # Materialize and detach the bounded SQL inventory before filesystem/qB I/O.
    # expire_on_commit=False keeps these scalar model fields available without SQL.
    await db.commit()
    try:
        storage = await run_in_threadpool(
            SharedContentStore(settings.data_root).inventory,
            limit=limit,
        )
    except SharedContentStoreError:
        storage = None

    qbittorrent: QBittorrentV2Inventory | None = None
    if (
        settings.qbittorrent_url is not None
        and settings.qbittorrent_username is not None
        and settings.qbittorrent_password is not None
    ):
        timeout = httpx.Timeout(
            connect=settings.integration_connect_timeout_seconds,
            read=settings.integration_read_timeout_seconds,
            write=settings.integration_read_timeout_seconds,
            pool=settings.integration_connect_timeout_seconds,
        )
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                gateway = QBittorrentV2Gateway(
                    client,
                    str(settings.qbittorrent_url),
                    settings.qbittorrent_username,
                    settings.qbittorrent_password.get_secret_value(),
                    data_root=settings.qbittorrent_data_root,
                )
                qbittorrent = await gateway.inventory_torrents(limit=limit)
        except (httpx.HTTPError, RuntimeError):
            qbittorrent = None

    report = reconcile_inventory(
        torrents,
        database_truncated=database_truncated,
        qbittorrent=qbittorrent,
        storage=storage,
    )
    return AdminV2ReconciliationReport(
        database_scanned=report.database_scanned,
        qbittorrent_scanned=report.qbittorrent_scanned,
        storage_scanned=report.storage_scanned,
        external_torrents=report.external_torrents,
        anomalies=[
            AdminV2ReconciliationAnomaly(
                code=item.code,
                severity=item.severity,
                resource_id=item.resource_id,
                action=item.action,
            )
            for item in report.anomalies
        ],
        truncated=report.truncated,
    )


@router.post(
    "/reconciliation/{managed_torrent_id}/recover",
    response_model=AdminV2RecoveryResult,
)
async def recover_admin_managed_torrent(
    managed_torrent_id: uuid.UUID,
    payload: AdminV2RecoveryRequest,
    db: DbSession,
    settings: AppSettings,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> AdminV2RecoveryResult:
    try:
        expected = await recovery_snapshot(db, managed_torrent_id)
    except ReconciliationRecoveryError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": str(exc)},
        ) from exc
    identity = QBittorrentV2ManagedIdentity(expected.info_hash, expected.storage_key)
    await db.commit()
    try:
        storage_present = await run_in_threadpool(
            SharedContentStore(settings.data_root).contains,
            expected.storage_key,
        )
        timeout = httpx.Timeout(
            connect=settings.integration_connect_timeout_seconds,
            read=settings.integration_read_timeout_seconds,
            write=settings.integration_read_timeout_seconds,
            pool=settings.integration_connect_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            if (
                settings.integration_accounts_json is not None
                and expected.qbittorrent_account_ref is not None
            ):
                account_router = build_deployment_account_router(
                    settings.integration_accounts_json,
                    client,
                    session_factory,
                    allowed_tracker_hosts=settings.c411_tracker_hosts,
                    data_root=settings.qbittorrent_data_root,
                    max_total_size=MAX_MANAGED_TORRENT_BYTES,
                )
                qbittorrent_present = await account_router.managed_torrent_is_present(
                    expected.qbittorrent_account_ref,
                    identity,
                )
            elif (
                settings.qbittorrent_url is not None
                and settings.qbittorrent_username is not None
                and settings.qbittorrent_password is not None
            ):
                gateway = QBittorrentV2Gateway(
                    client,
                    str(settings.qbittorrent_url),
                    settings.qbittorrent_username,
                    settings.qbittorrent_password.get_secret_value(),
                    data_root=settings.qbittorrent_data_root,
                )
                try:
                    qbittorrent_present = (
                        len(await gateway.inspect_managed_torrents((identity,))) == 1
                    )
                except QBittorrentV2MissingError:
                    qbittorrent_present = False
            else:
                raise RuntimeError("qbittorrent_inventory_unavailable")
    except (
        httpx.HTTPError,
        IntegrationRequestError,
        RuntimeError,
        SharedContentStoreError,
    ) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "reconciliation_evidence_unavailable"},
        ) from exc

    try:
        result = await recover_orphaned_torrent(
            db,
            managed_torrent_id,
            action=payload.action,
            qbittorrent_present=qbittorrent_present,
            storage_present=storage_present,
            expected=expected,
        )
        await db.commit()
    except ReconciliationRecoveryError as exc:
        await db.rollback()
        code = str(exc)
        response_status = (
            status.HTTP_404_NOT_FOUND
            if code == "managed_torrent_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(response_status, detail={"code": code}) from exc
    return AdminV2RecoveryResult(
        managed_torrent_id=str(result.managed_torrent_id),
        state=result.state.value,
        cancelled_requests=result.cancelled_requests,
        metadata_purged=result.metadata_purged,
        qbittorrent_present=result.qbittorrent_present,
        storage_present=result.storage_present,
    )
