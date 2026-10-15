import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.api.external.dependencies import lock_external_client_authorization, require_scope
from app.api.external.errors import ExternalApiError
from app.api.external.schemas import (
    DiscordUserId,
    ExternalCapacityResponse,
    ExternalErrorResponse,
    ExternalUserCreateRequest,
    ExternalUserCreateResponse,
    ExternalUserStatusResponse,
)
from app.auth.dependencies import DbSession
from app.auth.security import hash_token
from app.models import (
    DiscordAccountLink,
    ExternalApiAudit,
    ExternalApiClient,
    ExternalApiIdempotency,
    User,
    UserSession,
)
from app.users import UserAccountQuotaReachedError, UserProvisioningService
from app.users.provisioning import UserProvisioningConflictError

router = APIRouter()


def _replay_response(
    existing: ExternalApiIdempotency, request_hash: str, response: Response
) -> ExternalUserCreateResponse:
    if existing.request_hash != request_hash:
        raise ExternalApiError(
            409, "idempotency_conflict", "Idempotency key reused with a different request"
        )
    response.status_code = status.HTTP_200_OK
    response.headers["Cache-Control"] = "no-store"
    return ExternalUserCreateResponse(
        id=existing.user.id,
        username=existing.user.username,
        temporary_password=None,
        auth_seed=existing.user.auth_seed,
        must_change_credentials=existing.user.must_change_credentials,
        idempotent_replay=True,
    )


@router.post(
    "/users",
    response_model=ExternalUserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ExternalErrorResponse},
        403: {"model": ExternalErrorResponse},
        409: {"model": ExternalErrorResponse},
        422: {"model": ExternalErrorResponse},
        429: {"model": ExternalErrorResponse},
    },
    summary="Create a normal WOS user",
)
async def create_external_user(
    payload: ExternalUserCreateRequest,
    request: Request,
    response: Response,
    db: DbSession,
    client: Annotated[ExternalApiClient, Depends(require_scope("users:create"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ExternalUserCreateResponse:
    if idempotency_key is None or not 8 <= len(idempotency_key) <= 200:
        raise ExternalApiError(422, "validation_error", "A valid Idempotency-Key is required")
    key_hash = hash_token(idempotency_key)
    request_body = payload.model_dump()
    if payload.discord_user_id is None:
        del request_body["discord_user_id"]
    request_hash = sha256(
        json.dumps(request_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    client_id, client_key_hash = client.id, client.key_hash
    provisioning = UserProvisioningService()
    lookup = (
        select(ExternalApiIdempotency)
        .options(selectinload(ExternalApiIdempotency.user))
        .where(
            ExternalApiIdempotency.client_id == client_id,
            ExternalApiIdempotency.key_hash == key_hash,
        )
        .execution_options(populate_existing=True)
    )
    existing = await db.scalar(lookup)
    if existing is not None:
        await lock_external_client_authorization(
            db, client_id=client_id, key_hash=client_key_hash, scope="users:create"
        )
        result_response = _replay_response(existing, request_hash, response)
        await db.commit()
        return result_response
    # No SQL connection or quota/idempotency lock is retained while hashing.
    await db.commit()
    prepared_password = await provisioning.prepare_password()
    await provisioning.lock_creation(db)
    await lock_external_client_authorization(
        db, client_id=client_id, key_hash=client_key_hash, scope="users:create"
    )
    existing = await db.scalar(lookup)
    if existing is not None:
        result_response = _replay_response(existing, request_hash, response)
        await db.commit()
        return result_response
    if payload.discord_user_id is not None:
        link = await db.get(DiscordAccountLink, payload.discord_user_id)
        if link is not None:
            await db.rollback()
            raise ExternalApiError(
                409, "discord_account_exists", "Discord account is already linked"
            )
    try:
        result = await provisioning.provision(
            db,
            username=payload.username,
            prepared_password=prepared_password,
            source="external_api",
            external_client_id=client_id,
        )
    except UserAccountQuotaReachedError as exc:
        await db.rollback()
        raise ExternalApiError(409, "account_quota_reached", "Account quota reached") from exc
    except (UserProvisioningConflictError, ValueError) as exc:
        await db.rollback()
        raise ExternalApiError(409, "username_conflict", "Username is unavailable") from exc

    request_id = request.state.external_request_id
    if payload.discord_user_id is not None:
        db.add(
            DiscordAccountLink(
                discord_user_id=payload.discord_user_id,
                user_id=result.user.id,
                client_id=client_id,
            )
        )
    db.add(
        ExternalApiIdempotency(
            client_id=client_id,
            key_hash=key_hash,
            request_hash=request_hash,
            created_user_id=result.user.id,
        )
    )
    db.add(
        ExternalApiAudit(
            client_id=client_id,
            user_id=result.user.id,
            action="users.create",
            request_id=request_id,
            idempotency_fingerprint=key_hash[:16],
        )
    )
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return ExternalUserCreateResponse(
        id=result.user.id,
        username=result.user.username,
        temporary_password=result.initial_password,
        auth_seed=result.user.auth_seed,
        must_change_credentials=result.user.must_change_credentials,
    )


@router.get("/capacity", response_model=ExternalCapacityResponse)
async def external_capacity(
    response: Response,
    db: DbSession,
    _client: Annotated[ExternalApiClient, Depends(require_scope("users:read"))],
) -> ExternalCapacityResponse:
    used, maximum = await UserProvisioningService().quota(db)
    await db.rollback()
    response.headers["Cache-Control"] = "no-store"
    return ExternalCapacityResponse(
        used=used,
        maximum=maximum,
        available=max(0, maximum - used),
        reached=used >= maximum,
    )


@router.get("/users/discord/{discord_user_id}", response_model=ExternalUserStatusResponse)
async def external_discord_status(
    discord_user_id: DiscordUserId,
    response: Response,
    db: DbSession,
    _client: Annotated[ExternalApiClient, Depends(require_scope("users:read"))],
) -> ExternalUserStatusResponse:
    user = await db.scalar(
        select(User)
        .join(
            DiscordAccountLink,
            DiscordAccountLink.user_id == User.id,
        )
        .where(DiscordAccountLink.discord_user_id == discord_user_id)
    )
    if user is None:
        raise ExternalApiError(404, "discord_account_not_found", "Discord account is not linked")
    response.headers["Cache-Control"] = "no-store"
    result = ExternalUserStatusResponse(
        id=user.id,
        username=user.username,
        is_active=user.is_active and user.deleted_at is None,
        deleted=user.deleted_at is not None,
        must_change_credentials=user.must_change_credentials,
    )
    await db.rollback()
    return result


@router.post(
    "/users/discord/{discord_user_id}/initial-credentials",
    response_model=ExternalUserCreateResponse,
)
async def recover_initial_credentials(
    discord_user_id: DiscordUserId,
    request: Request,
    response: Response,
    db: DbSession,
    client: Annotated[ExternalApiClient, Depends(require_scope("users:recover"))],
) -> ExternalUserCreateResponse:
    client_id, key_hash = client.id, client.key_hash
    # Hash outside any transaction, then revalidate authorization and account under locks.
    await db.rollback()
    prepared = await UserProvisioningService().prepare_password()
    await lock_external_client_authorization(
        db,
        client_id=client_id,
        key_hash=key_hash,
        scope="users:recover",
    )
    link = await db.get(DiscordAccountLink, discord_user_id)
    if link is None or link.client_id != client_id:
        await db.rollback()
        raise ExternalApiError(404, "discord_account_not_found", "Discord account is not linked")
    user = await db.get(User, link.user_id, with_for_update=True, populate_existing=True)
    if (
        user is None
        or user.is_admin
        or not user.is_active
        or user.deleted_at is not None
        or user.last_login_at is not None
        or not user.must_change_credentials
    ):
        await db.rollback()
        raise ExternalApiError(
            409, "initial_credentials_unavailable", "Initial recovery is unavailable"
        )
    now = datetime.now(UTC)
    user.password_hash = prepared.encoded_hash
    user.updated_at = now
    await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    db.add(
        ExternalApiAudit(
            client_id=client_id,
            user_id=user.id,
            action="users.recover_initial",
            request_id=request.state.external_request_id,
        )
    )
    result = ExternalUserCreateResponse(
        id=user.id,
        username=user.username,
        temporary_password=prepared.initial_password,
        auth_seed=user.auth_seed,
        must_change_credentials=True,
    )
    await db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result
