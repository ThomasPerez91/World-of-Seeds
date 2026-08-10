from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.auth.dependencies import (
    AppSettings,
    AuthContext,
    DbSession,
    get_auth_context,
    require_csrf,
)
from app.auth.service import (
    AuthenticationFailedError,
    AuthenticationLockedError,
    SessionTokens,
    UsernameUnavailableError,
    authenticate,
    change_credentials,
    revoke_session,
)
from app.core.config import Settings
from app.schemas.auth import AuthResponse, ChangeCredentialsRequest, LoginRequest, UserResponse

router = APIRouter()


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
        key=settings.csrf_cookie_name,
        value=tokens.csrf_token,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
        max_age=max_age,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


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
            detail="Too many authentication attempts",
        ) from exc
    except AuthenticationFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
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
        )
    except AuthenticationFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid current password",
        ) from exc
    except (UsernameUnavailableError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    set_auth_cookies(response, tokens, settings)
    return AuthResponse(user=UserResponse.model_validate(context.user))
