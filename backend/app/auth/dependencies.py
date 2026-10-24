import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

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
    session_id: uuid.UUID | None = None


MAX_REALTIME_CONNECTIONS_PER_USER = 5
MAX_REALTIME_CONNECTIONS = 1000


class RealtimeConnectionLimiter:
    """Process-local cap on open event sockets (one API process serves production)."""

    def __init__(
        self,
        *,
        per_user: int = MAX_REALTIME_CONNECTIONS_PER_USER,
        total: int = MAX_REALTIME_CONNECTIONS,
    ) -> None:
        self._per_user_limit = per_user
        self._total_limit = total
        self._per_user: dict[uuid.UUID, int] = {}
        self._total = 0

    def acquire(self, user_id: uuid.UUID) -> bool:
        if (
            self._total >= self._total_limit
            or self._per_user.get(user_id, 0) >= self._per_user_limit
        ):
            return False
        self._per_user[user_id] = self._per_user.get(user_id, 0) + 1
        self._total += 1
        return True

    def release(self, user_id: uuid.UUID) -> None:
        remaining = self._per_user.get(user_id, 0) - 1
        if remaining > 0:
            self._per_user[user_id] = remaining
        else:
            self._per_user.pop(user_id, None)
        self._total = max(0, self._total - 1)


realtime_connections = RealtimeConnectionLimiter()


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    """A browser always sends Origin: only the page's own host may open the socket.

    SameSite already keeps cross-site pages out; this also refuses a sibling sub-domain that
    shares the site (for instance the public Grafana). Clients without Origin are not browsers
    and still need the session cookie.
    """
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    try:
        origin_host = urlsplit(origin).hostname
        request_host = urlsplit(f"//{websocket.headers.get('host', '')}").hostname
    except ValueError:
        return False
    return origin_host is not None and origin_host == request_host


def _detail(code: str, message: str) -> dict[str, str | None]:
    return {"code": code, "message": message, "field": None}


async def get_auth_context(
    request: Request,
    db: DbSession,
    settings: AppSettings,
) -> AuthContext:
    token = request.cookies.get(settings.session_cookie_name)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_detail("not_authenticated", "Not authenticated"),
        )

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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_detail("not_authenticated", "Not authenticated"),
        )

    # Authentication is read-only. End its transaction before entering the route so
    # long filesystem streams never retain a PostgreSQL pool connection.
    await db.commit()
    return AuthContext(user=user_session.user, session=user_session)


async def get_realtime_auth_context(
    websocket: WebSocket,
    settings: AppSettings,
) -> RealtimeAuthContext:
    if not websocket_origin_allowed(websocket):
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
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
        session_id = user_session.id
        await db.rollback()
    # Only opaque identifiers survive authentication. The SQL session is closed before
    # the WebSocket is accepted and before any Redis wait or network heartbeat begins.
    return RealtimeAuthContext(user_id, session_id)


async def get_realtime_connection(
    context: Annotated[RealtimeAuthContext, Depends(get_realtime_auth_context)],
) -> AsyncGenerator[RealtimeAuthContext, None]:
    """Authenticate, then hold one of the user's bounded event-socket slots."""
    if not realtime_connections.acquire(context.user_id):
        raise WebSocketException(code=status.WS_1013_TRY_AGAIN_LATER)
    try:
        yield context
    finally:
        realtime_connections.release(context.user_id)


async def realtime_session_is_valid(context: RealtimeAuthContext) -> bool:
    """Short read-only check: a socket must not outlive its logout, suspension or expiry."""
    if context.session_id is None:
        return True
    async with session_factory() as db:
        user_session = await db.scalar(
            select(UserSession)
            .options(selectinload(UserSession.user))
            .where(UserSession.id == context.session_id)
        )
        now = datetime.now(UTC)
        valid = (
            user_session is not None
            and user_session.revoked_at is None
            and ensure_utc(user_session.expires_at) > now
            and user_can_login(user_session.user, now)
            and not user_session.user.must_change_credentials
        )
        await db.rollback()
    return bool(valid)


async def require_current_credentials(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    if context.user.must_change_credentials:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_detail("credentials_change_required", "Credential change required"),
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
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_detail("csrf_invalid", "Invalid CSRF token"),
        )
    return context


async def require_current_credentials_csrf(
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> AuthContext:
    if context.user.must_change_credentials:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_detail("credentials_change_required", "Credential change required"),
        )
    return context


async def require_admin_csrf(
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> AuthContext:
    if context.user.must_change_credentials or not context.user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_detail("forbidden", "Forbidden"),
        )
    return context


async def require_current_admin(
    context: Annotated[AuthContext, Depends(require_current_credentials)],
) -> AuthContext:
    if not context.user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_detail("forbidden", "Forbidden"),
        )
    return context
