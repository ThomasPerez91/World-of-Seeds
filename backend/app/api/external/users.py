import json
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.external.dependencies import require_scope
from app.api.external.errors import ExternalApiError
from app.api.external.schemas import (
    ExternalErrorResponse,
    ExternalUserCreateRequest,
    ExternalUserCreateResponse,
)
from app.auth.dependencies import DbSession
from app.auth.security import hash_token
from app.models import ExternalApiAudit, ExternalApiClient, ExternalApiIdempotency
from app.users import UserAccountQuotaReachedError, UserProvisioningService
from app.users.provisioning import UserProvisioningConflictError

router = APIRouter()


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
    request_hash = sha256(
        json.dumps(payload.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    provisioning = UserProvisioningService()
    await provisioning.lock_creation(db)
    existing = await db.scalar(
        select(ExternalApiIdempotency)
        .options(selectinload(ExternalApiIdempotency.user))
        .where(
            ExternalApiIdempotency.client_id == client.id,
            ExternalApiIdempotency.key_hash == key_hash,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ExternalApiError(
                409, "idempotency_conflict", "Idempotency key reused with a different request"
            )
        response.status_code = status.HTTP_200_OK
        response.headers["Cache-Control"] = "no-store"
        await db.commit()
        return ExternalUserCreateResponse(
            id=existing.user.id,
            username=existing.user.username,
            temporary_password=None,
            auth_seed=existing.user.auth_seed,
            must_change_credentials=existing.user.must_change_credentials,
            idempotent_replay=True,
        )
    try:
        result = await provisioning.provision(
            db,
            username=payload.username,
            source="external_api",
            external_client_id=client.id,
        )
    except UserAccountQuotaReachedError as exc:
        await db.rollback()
        raise ExternalApiError(409, "account_quota_reached", "Account quota reached") from exc
    except (UserProvisioningConflictError, ValueError) as exc:
        await db.rollback()
        raise ExternalApiError(409, "username_conflict", "Username is unavailable") from exc

    request_id = request.state.external_request_id
    db.add(
        ExternalApiIdempotency(
            client_id=client.id,
            key_hash=key_hash,
            request_hash=request_hash,
            created_user_id=result.user.id,
        )
    )
    db.add(
        ExternalApiAudit(
            client_id=client.id,
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
