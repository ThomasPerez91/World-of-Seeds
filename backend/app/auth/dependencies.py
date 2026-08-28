import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.security import hash_token, tokens_match
from app.auth.service import ensure_utc, user_can_login
from app.core.config import CSRF_COOKIE_NAME, Settings, get_settings
from app.core.database import get_db_session, session_factory
from app.models import User, UserSession

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: UserSession


@dataclass(frozen=True, slots=True)
class RealtimeAuthContext:
    user_id: uuid.UUID


async def get_auth_context(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AuthContext:
    token = request.cookies.get(settings.session_cookie_name)
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_session = await db.scalar(
        select(UserSession)
        .options(selectinload(UserSession.user))
        .where(UserSession.token_hash == hash_token(token))
    )
    now = datetime.now(UTC)
    if (
        user_session is None
        or user_session.revoked_at is not None
        or ensure_utc(user_session.expires_at) <= now
        or not user_can_login(user_session.user, now)
    ):
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # Authentication is read-only. End its transaction before entering the route so
    # long filesystem streams never retain a PostgreSQL pool connection.
    await db.commit()
    return AuthContext(user=user_session.user, session=user_session)


async def get_realtime_auth_context(
    websocket: WebSocket,
    settings: AppSettings,
) -> RealtimeAuthContext:
    token = websocket.cookies.get(settings.session_cookie_name)
    if token is None:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    async with session_factory() as db:
        user_session = await db.scalar(
            select(UserSession)
            .options(selectinload(UserSession.user))
            .where(UserSession.token_hash == hash_token(token))
        )
        now = datetime.now(UTC)
        if (
            user_session is None
            or user_session.revoked_at is not None
            or ensure_utc(user_session.expires_at) <= now
            or not user_can_login(user_session.user, now)
            or user_session.user.must_change_credentials
        ):
            raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
        user_id = user_session.user_id
        await db.rollback()
    # Only the opaque user ID survives authentication. The SQL session is closed before
    # the WebSocket is accepted and before any Redis wait or network heartbeat begins.
    return RealtimeAuthContext(user_id)


async def require_current_credentials(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    if context.user.must_change_credentials:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Credential change required",
        )
    return context


async def require_csrf(
    request: Request,
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get("X-CSRF-Token")
    if (
        cookie_token is None
        or header_token is None
        or not tokens_match(cookie_token, header_token)
        or not tokens_match(hash_token(header_token), context.session.csrf_token_hash)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
    return context


async def require_current_credentials_csrf(
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> AuthContext:
    if context.user.must_change_credentials:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Credential change required",
        )
    return context


async def require_admin_csrf(
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> AuthContext:
    if context.user.must_change_credentials or not context.user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return context


async def require_current_admin(
    context: Annotated[AuthContext, Depends(require_current_credentials)],
) -> AuthContext:
    if not context.user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    return context
