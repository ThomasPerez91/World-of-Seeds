from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select

from app.api.external.schemas import (
    ExternalApiClientCreatedResponse,
    ExternalApiClientCreateRequest,
    ExternalApiClientResponse,
    ExternalScope,
)
from app.auth.dependencies import AuthContext, DbSession, require_admin_csrf, require_current_admin
from app.auth.security import generate_token, hash_token
from app.models import ExternalApiClient

router = APIRouter()


def _response(client: ExternalApiClient) -> ExternalApiClientResponse:
    return ExternalApiClientResponse(
        id=client.id,
        name=client.name,
        key_prefix=client.key_prefix,
        is_active=client.is_active,
        scopes=cast(list[ExternalScope], client.scopes),
        created_at=client.created_at,
        updated_at=client.updated_at,
        last_used_at=client.last_used_at,
        revoked_at=client.revoked_at,
    )


@router.get("", response_model=list[ExternalApiClientResponse])
async def list_clients(
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_current_admin)],
) -> list[ExternalApiClientResponse]:
    clients = (
        await db.scalars(select(ExternalApiClient).order_by(ExternalApiClient.created_at.desc()))
    ).all()
    return [_response(client) for client in clients]


@router.post(
    "", response_model=ExternalApiClientCreatedResponse, status_code=status.HTTP_201_CREATED
)
async def create_client(
    payload: ExternalApiClientCreateRequest,
    response: Response,
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> ExternalApiClientCreatedResponse:
    raw_key = f"wos_live_{generate_token()}"
    scopes = sorted(set(payload.scopes))
    client = ExternalApiClient(
        name=payload.name.strip(),
        key_hash=hash_token(raw_key),
        key_prefix=f"{raw_key[:18]}…",
        scopes=scopes,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    response.headers["Cache-Control"] = "no-store"
    return ExternalApiClientCreatedResponse(**_response(client).model_dump(), api_key=raw_key)


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_client(
    client_id: UUID,
    db: DbSession,
    _: Annotated[AuthContext, Depends(require_admin_csrf)],
) -> None:
    client = await db.get(ExternalApiClient, client_id)
    if client is not None and client.revoked_at is None:
        client.is_active = False
        client.revoked_at = datetime.now(UTC)
        await db.commit()
