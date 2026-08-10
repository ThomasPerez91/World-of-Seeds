from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.auth.dependencies import AuthContext, DbSession, require_admin_csrf, require_current_admin
from app.auth.service import create_temporary_user
from app.files import WorkspaceError
from app.files.dependencies import WorkspaceManagerDependency
from app.models import User
from app.schemas.auth import (
    TemporaryCredentialsResponse,
    TemporaryUserRequest,
    UserResponse,
)

router = APIRouter()


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> list[UserResponse]:
    users = (await db.scalars(select(User).order_by(User.created_at.desc()))).all()
    return [UserResponse.model_validate(user) for user in users]


@router.post("/users/temporary", response_model=TemporaryCredentialsResponse)
async def generate_temporary_user(
    payload: TemporaryUserRequest,
    db: DbSession,
    workspace_manager: WorkspaceManagerDependency,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> TemporaryCredentialsResponse:
    try:
        user, temporary_password = await create_temporary_user(
            db,
            expires_in_days=payload.expires_in_days,
            workspace_manager=workspace_manager,
        )
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User storage is unavailable",
        ) from exc
    return TemporaryCredentialsResponse(
        user=UserResponse.model_validate(user),
        temporary_password=temporary_password,
    )
