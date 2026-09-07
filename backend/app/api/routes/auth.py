from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.auth.dependencies import (
    AppSettings,
    AuthContext,
    DbSession,
    get_auth_context,
    require_csrf,
    require_current_credentials_csrf,
)
from app.auth.service import (
    AuthenticationFailedError,
    AuthenticationLockedError,
    SessionTokens,
    UsernameUnavailableError,
    authenticate,
    change_credentials,
    change_password,
    change_username,
    revoke_session,
)
from app.core.config import CSRF_COOKIE_NAME, Settings
from app.files import WorkspaceError
from app.files.dependencies import WorkspaceManagerDependency
from app.schemas.auth import (
    AuthResponse,
    ChangeCredentialsRequest,
    ChangeLocaleRequest,
    ChangePasswordRequest,
    ChangeUsernameRequest,
    LoginRequest,
    UserResponse,
)

router = APIRouter()


def _detail(code: str, message: str, field: str | None = None) -> dict[str, str | None]:
    return {"code": code, "message": message, "field": field}


def set_auth_cookies(response: Response, tokens: SessionTokens, settings: Settings) -> None:
    max_age = settings.session_ttl_hours * 60 * 60
    response.set_cookie(
        key=settings.session_cookie_name,
        value=tokens.session_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        max_age=max_age,
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=tokens.csrf_token,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        max_age=max_age,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
) -> AuthResponse:
    client_ip = request.client.host if request.client is not None else "unknown"
    try:
        user, tokens = await authenticate(
            db,
            username_input=payload.username,
            password=payload.password,
            client_ip=client_ip,
            settings=settings,
        )
    except AuthenticationLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_detail("authentication_throttled", "Too many authentication attempts"),
        ) from exc
    except AuthenticationFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_detail("authentication_failed", "Invalid username or password"),
        ) from exc

    set_auth_cookies(response, tokens, settings)
    return AuthResponse(user=UserResponse.model_validate(user))


@router.get("/me", response_model=AuthResponse)
async def me(
    context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthResponse:
    return AuthResponse(user=UserResponse.model_validate(context.user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    db: DbSession,
    settings: AppSettings,
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> None:
    await revoke_session(db, context.session)
    clear_auth_cookies(response, settings)


@router.patch("/credentials", response_model=AuthResponse)
async def update_credentials(
    payload: ChangeCredentialsRequest,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    workspace_manager: WorkspaceManagerDependency,
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> AuthResponse:
    try:
        tokens = await change_credentials(
            db,
            user=context.user,
            current_password=payload.current_password,
            username_input=payload.username,
            new_password=payload.new_password,
            settings=settings,
            workspace_manager=workspace_manager,
        )
    except AuthenticationFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_detail(
                "current_password_invalid", "Invalid current password", "current_password"
            ),
        ) from exc
    except (UsernameUnavailableError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail("username_unavailable", str(exc), "username"),
        ) from exc
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail("user_workspace_unavailable", "User workspace is unavailable"),
        ) from exc

    set_auth_cookies(response, tokens, settings)
    return AuthResponse(user=UserResponse.model_validate(context.user))


@router.patch("/username", response_model=AuthResponse)
async def update_username(
    payload: ChangeUsernameRequest,
    db: DbSession,
    workspace_manager: WorkspaceManagerDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
) -> AuthResponse:
    try:
        user = await change_username(
            db,
            user=context.user,
            username_input=payload.username,
            workspace_manager=workspace_manager,
        )
    except AuthenticationFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_detail("not_authenticated", "Not authenticated"),
        ) from exc
    except (UsernameUnavailableError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail("username_unavailable", str(exc), "username"),
        ) from exc
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_detail("user_workspace_unavailable", "User workspace is unavailable"),
        ) from exc
    return AuthResponse(user=UserResponse.model_validate(user))


@router.patch("/locale", response_model=AuthResponse)
async def update_locale(
    payload: ChangeLocaleRequest,
    db: DbSession,
    context: Annotated[AuthContext, Depends(require_csrf)],
) -> AuthResponse:
    context.user.preferred_locale = payload.preferred_locale
    await db.commit()
    return AuthResponse(user=UserResponse.model_validate(context.user))


@router.patch("/password", status_code=status.HTTP_204_NO_CONTENT)
async def update_password(
    payload: ChangePasswordRequest,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
) -> None:
    try:
        await change_password(
            db,
            user=context.user,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except AuthenticationFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_detail(
                "current_password_invalid", "Invalid current password", "current_password"
            ),
        ) from exc
    clear_auth_cookies(response, settings)
