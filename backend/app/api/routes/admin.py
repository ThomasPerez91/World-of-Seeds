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
from app.trash.dependencies import TrashServiceDependency
from app.trash.filesystem import TrashPurgeError, TrashStorageError, TrashStorageUnsafeError
from app.trash.service import TrashEntryNotFoundError, TrashPersistenceError

router = APIRouter()


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
