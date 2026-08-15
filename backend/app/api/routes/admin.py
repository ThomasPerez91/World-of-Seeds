from typing import Annotated, Literal, Never, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.admin import AdminStorageError, AdminStorageInspector
from app.auth.dependencies import AuthContext, DbSession, require_admin_csrf, require_current_admin
from app.auth.service import (
    ManagedUserNotFoundError,
    ProtectedUserError,
    create_managed_user,
    delete_managed_user,
    set_managed_user_active,
)
from app.core.config import Settings, get_settings
from app.files import WorkspaceError
from app.files.dependencies import WorkspaceManagerDependency
from app.integrations.dependencies import (
    ExternalServicesMonitorDependency,
    NewGreedyConfigStoreDependency,
)
from app.integrations.http import IntegrationRequestError
from app.integrations.newgreedy_config import (
    ConfigFieldValue,
    NewGreedyConfigError,
    NewGreedyConfigValidationError,
)
from app.models import TrashEntry, User
from app.schemas.admin import (
    AdminStorageResponse,
    AdminTrashEntryResponse,
    AdminTrashListingResponse,
    AdminTrashPurgeResponse,
)
from app.schemas.auth import (
    GeneratedCredentialsResponse,
    UserResponse,
    UserStatusRequest,
)
from app.schemas.health import AdminSystemHealthResponse, ServiceHealthDetail
from app.schemas.integrations import (
    SECTION_LABELS,
    NewGreedyConfigFieldResponse,
    NewGreedyConfigResponse,
    NewGreedyConfigSectionResponse,
    NewGreedyConfigUpdateRequest,
    NewGreedyOverviewResponse,
    NewGreedyStatsResetResponse,
)
from app.trash.dependencies import TrashServiceDependency
from app.trash.filesystem import TrashPurgeError, TrashStorageError, TrashStorageUnsafeError
from app.trash.service import TrashEntryNotFoundError, TrashPersistenceError

router = APIRouter()


@router.get("/services/health", response_model=AdminSystemHealthResponse)
async def get_services_health(
    monitor: ExternalServicesMonitorDependency,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> AdminSystemHealthResponse:
    snapshot = await monitor.snapshot()
    return AdminSystemHealthResponse(
        status="ok" if snapshot.healthy else "degraded",
        checked_at=snapshot.checked_at,
        newgreedy=ServiceHealthDetail(
            status=snapshot.newgreedy.state,
            latency_ms=snapshot.newgreedy.latency_ms,
            version=snapshot.newgreedy.version,
            error_code=snapshot.newgreedy.error_code,
        ),
        qbittorrent=ServiceHealthDetail(
            status=snapshot.qbittorrent.state,
            latency_ms=snapshot.qbittorrent.latency_ms,
            version=snapshot.qbittorrent.version,
            error_code=snapshot.qbittorrent.error_code,
        ),
    )


def _config_response(
    fields: list[ConfigFieldValue], *, restart_required: bool = False
) -> NewGreedyConfigResponse:
    grouped: dict[str, list[NewGreedyConfigFieldResponse]] = {}
    for field in fields:
        spec = field.spec
        grouped.setdefault(spec.section, []).append(
            NewGreedyConfigFieldResponse(
                id=spec.identifier,
                key=spec.key,
                label=spec.label,
                description=spec.description,
                input_type=spec.input_type,
                value=field.value,
                editable=spec.editable,
                minimum=spec.minimum,
                maximum=spec.maximum,
                options=list(spec.options),
            )
        )
    return NewGreedyConfigResponse(
        sections=[
            NewGreedyConfigSectionResponse(
                id=section,
                label=SECTION_LABELS.get(section, section),
                fields=section_fields,
            )
            for section, section_fields in grouped.items()
        ],
        restart_required=restart_required,
    )


def _raise_newgreedy_config_error(exc: NewGreedyConfigError) -> Never:
    if isinstance(exc, NewGreedyConfigValidationError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="NewGreedy configuration is invalid",
        ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="NewGreedy configuration is unavailable",
    ) from exc


@router.get("/services/newgreedy/config", response_model=NewGreedyConfigResponse)
async def get_newgreedy_config(
    store: NewGreedyConfigStoreDependency,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> NewGreedyConfigResponse:
    try:
        fields = await run_in_threadpool(store.read)
    except NewGreedyConfigError as exc:
        _raise_newgreedy_config_error(exc)
    return _config_response(fields)


@router.patch("/services/newgreedy/config", response_model=NewGreedyConfigResponse)
async def update_newgreedy_config(
    payload: NewGreedyConfigUpdateRequest,
    store: NewGreedyConfigStoreDependency,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> NewGreedyConfigResponse:
    try:
        fields = await run_in_threadpool(store.update, payload.changes)
    except NewGreedyConfigError as exc:
        _raise_newgreedy_config_error(exc)
    return _config_response(fields, restart_required=True)


@router.get("/services/newgreedy/overview", response_model=NewGreedyOverviewResponse)
async def get_newgreedy_overview(
    monitor: ExternalServicesMonitorDependency,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> NewGreedyOverviewResponse:
    try:
        overview = await monitor.newgreedy_overview()
    except IntegrationRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NewGreedy statistics are unavailable",
        ) from exc
    return NewGreedyOverviewResponse(
        torrents=overview.torrents,
        downloading=overview.downloading,
        seeding=overview.seeding,
        stalled=overview.stalled,
        target_reached=overview.target_reached,
        total_downloaded_bytes=overview.total_downloaded_bytes,
        total_reported_uploaded_bytes=overview.total_reported_uploaded_bytes,
        total_fake_uploaded_bytes=overview.total_fake_uploaded_bytes,
    )


@router.delete(
    "/services/newgreedy/stats",
    response_model=NewGreedyStatsResetResponse,
)
async def reset_newgreedy_stats(
    monitor: ExternalServicesMonitorDependency,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> NewGreedyStatsResetResponse:
    try:
        result = await monitor.reset_newgreedy_stats()
    except IntegrationRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NewGreedy statistics could not be reset",
        ) from exc
    return NewGreedyStatsResetResponse(purged=result.purged, remaining=result.remaining)


def _raise_admin_trash_error(exc: Exception) -> Never:
    if isinstance(exc, TrashEntryNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trash entry not found")
    if isinstance(exc, TrashStorageUnsafeError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Trash entry failed its integrity check",
        ) from exc
    if isinstance(exc, TrashPurgeError):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Permanent deletion did not complete",
        ) from exc
    if isinstance(exc, (TrashPersistenceError, TrashStorageError)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trash storage is unavailable",
        ) from exc
    raise exc


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> list[UserResponse]:
    users = (
        await db.scalars(
            select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc())
        )
    ).all()
    return [UserResponse.model_validate(user) for user in users]


@router.post("/users", response_model=GeneratedCredentialsResponse)
async def generate_user(
    db: DbSession,
    workspace_manager: WorkspaceManagerDependency,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> GeneratedCredentialsResponse:
    try:
        user, initial_password = await create_managed_user(
            db,
            workspace_manager=workspace_manager,
        )
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User storage is unavailable",
        ) from exc
    return GeneratedCredentialsResponse(
        user=UserResponse.model_validate(user),
        initial_password=initial_password,
    )


@router.patch("/users/{user_id}/status", response_model=UserResponse)
async def update_user_status(
    user_id: UUID,
    payload: UserStatusRequest,
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> UserResponse:
    try:
        user = await set_managed_user_active(db, user_id=user_id, is_active=payload.is_active)
    except ManagedUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
    except ProtectedUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator accounts cannot be suspended",
        ) from exc
    return UserResponse.model_validate(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> Response:
    try:
        await delete_managed_user(db, user_id=user_id)
    except ManagedUserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
    except ProtectedUserError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator accounts cannot be deleted",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/storage", response_model=AdminStorageResponse)
async def get_storage_overview(
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> AdminStorageResponse:
    try:
        usage = await run_in_threadpool(AdminStorageInspector(settings.data_root).inspect)
    except AdminStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Storage usage is unavailable",
        ) from exc

    active_users = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.deleted_at.is_(None), User.is_active.is_(True))
    )
    suspended_users = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.deleted_at.is_(None), User.is_active.is_(False))
    )
    trash_entries = await db.scalar(select(func.count()).select_from(TrashEntry))
    known_trash_bytes = await db.scalar(select(func.coalesce(func.sum(TrashEntry.size), 0)))
    return AdminStorageResponse(
        total=usage.total,
        used=usage.used,
        available=usage.available,
        active_users=int(active_users or 0),
        suspended_users=int(suspended_users or 0),
        trash_entries=int(trash_entries or 0),
        known_trash_bytes=int(known_trash_bytes or 0),
    )


@router.get("/trash", response_model=AdminTrashListingResponse)
async def list_all_trash(
    service: TrashServiceDependency,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> AdminTrashListingResponse:
    listing = await service.list_all_entries()
    return AdminTrashListingResponse(
        entries=[
            AdminTrashEntryResponse(
                id=item.entry.id,
                user_id=item.entry.user_id,
                username=item.username,
                original_path=item.entry.original_path,
                name=item.entry.name,
                kind=cast(Literal["directory", "file"], item.entry.kind),
                size=item.entry.size,
                deleted_at=item.entry.deleted_at,
            )
            for item in listing.entries
        ],
        truncated=listing.truncated,
    )


@router.delete("/trash/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def purge_any_trash_entry(
    entry_id: UUID,
    service: TrashServiceDependency,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> Response:
    try:
        await service.purge_any(entry_id)
    except Exception as exc:
        _raise_admin_trash_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/trash", response_model=AdminTrashPurgeResponse)
async def purge_all_trash(
    service: TrashServiceDependency,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> AdminTrashPurgeResponse:
    try:
        purged, remaining = await service.purge_batch()
    except Exception as exc:
        _raise_admin_trash_error(exc)
    return AdminTrashPurgeResponse(purged=purged, remaining=remaining)
