from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from app.auth.dependencies import AuthContext, DbSession, require_admin_csrf, require_current_admin
from app.auth.service import (
    ManagedUserNotFoundError,
    ProtectedUserError,
    create_managed_user,
    delete_managed_user,
    set_managed_user_active,
)
from app.files import WorkspaceError
from app.files.dependencies import WorkspaceManagerDependency
from app.models import User
from app.schemas.auth import (
    GeneratedCredentialsResponse,
    UserResponse,
    UserStatusRequest,
)

router = APIRouter()


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
