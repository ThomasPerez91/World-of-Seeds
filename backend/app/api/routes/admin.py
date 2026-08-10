from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select

from app.auth.dependencies import AuthContext, DbSession, require_admin_csrf, require_current_admin
from app.auth.service import create_temporary_user
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
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> TemporaryCredentialsResponse:
    user, temporary_password = await create_temporary_user(
        db,
        expires_in_days=payload.expires_in_days,
    )
    return TemporaryCredentialsResponse(
        user=UserResponse.model_validate(user),
        temporary_password=temporary_password,
    )
