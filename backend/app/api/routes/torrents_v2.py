from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal, Never, cast

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import (
    AuthContext,
    DbSession,
    require_current_credentials,
    require_current_credentials_csrf,
)
from app.coordination.dependencies import RedisCoordinatorDependency
from app.core.config import Settings, get_settings
from app.jobs.torrent_effects import ADD_TORRENT_JOB
from app.jobs.torrent_payloads import TorrentPayloadStore, TorrentPayloadStoreError
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
)
from app.options import DatabaseOptionsDriftError, PostgresOptionsRegistry
from app.schemas.torrents_v2 import (
    TorrentRequestV2CreateResponse,
    TorrentRequestV2ListingResponse,
    TorrentRequestV2Response,
)
from app.storage import (
    SharedContentStore,
    SharedContentStoreError,
    StorageAdmissionError,
    StorageAdmissionPolicy,
    StorageDiskSnapshot,
)
from app.torrents import (
    TorrentDeduplicationError,
    TorrentMetadataConflictError,
    TorrentValidationError,
    create_or_get_torrent_request,
    sanitize_torrent,
)

router = APIRouter()
UPLOAD_READ_CHUNK = 64 * 1024
MAX_PAGE_SIZE = 100
ACTIVE_REQUEST_STATES = (
    TorrentRequestState.REQUESTED,
    TorrentRequestState.ACTIVE,
    TorrentRequestState.READY,
)
RequestState = Literal["requested", "active", "ready", "cancelled", "expired", "error"]
PressureState = Literal["normal", "warning", "critical"]


def _detail(code: str, message: str, field: str | None = None) -> dict[str, str | None]:
    return {"code": code, "message": message, "field": field}


def _fail(status_code: int, code: str, message: str, field: str | None = None) -> Never:
    raise HTTPException(status_code=status_code, detail=_detail(code, message, field))


async def _read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    content = bytearray()
    while True:
        chunk = await upload.read(min(UPLOAD_READ_CHUNK, max_bytes + 1 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_bytes:
            _fail(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "torrent_too_large",
                "Le fichier .torrent dépasse la taille autorisée.",
                "torrent",
            )
    return bytes(content)


def _integer_option(values: dict[str, bool | int | str], key: str) -> int:
    value = values.get(key)
    if type(value) is not int:
        raise DatabaseOptionsDriftError("database torrent options are incomplete")
    return value


def _response(
    request: TorrentRequest,
    torrent: ManagedTorrent,
    *,
    error_code: str | None = None,
) -> TorrentRequestV2Response:
    request_state = request.state.value.lower()
    state = cast(
        RequestState,
        "error" if torrent.state is ManagedTorrentState.ERROR else request_state,
    )
    return TorrentRequestV2Response(
        id=request.id,
        name=torrent.name,
        total_size=torrent.total_size,
        state=state,
        progress=torrent.progress,
        error_code=(error_code or "torrent_failed") if state == "error" else None,
        created_at=request.created_at,
        updated_at=max(request.updated_at, torrent.updated_at),
    )


@router.post("", response_model=TorrentRequestV2CreateResponse, status_code=status.HTTP_201_CREATED)
async def create_torrent_request(
    db: DbSession,
    redis: RedisCoordinatorDependency,
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
    torrent: Annotated[UploadFile, File(description="Fichier BitTorrent C411")],
) -> TorrentRequestV2CreateResponse:
    user_id = context.user.id
    if torrent.filename is None or not torrent.filename.lower().endswith(".torrent"):
        _fail(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "torrent_filename_invalid",
            "Sélectionne un fichier portant l’extension .torrent.",
            "torrent",
        )

    registry = PostgresOptionsRegistry()
    try:
        values = await registry.snapshot(db)
        max_upload = _integer_option(values, "WOS_TORRENT_UPLOAD_MAX_FILE_BYTES")
        max_total = _integer_option(values, "WOS_TORRENT_MAX_SIZE_BYTES")
        max_active = _integer_option(values, "WOS_TORRENT_MAX_ACTIVE_PER_USER")
        policy = StorageAdmissionPolicy.from_options(values)
    except (DatabaseOptionsDriftError, ValueError):
        await db.rollback()
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "torrent_options_unavailable",
            "L’ajout de torrents est momentanément indisponible.",
        )
    await db.rollback()

    content = await _read_upload(torrent, max_upload)
    try:
        parsed = await run_in_threadpool(
            sanitize_torrent,
            content,
            allowed_tracker_hosts=settings.c411_tracker_hosts,
            max_total_size=max_total,
        )
    except TorrentValidationError as exc:
        _fail(status.HTTP_422_UNPROCESSABLE_CONTENT, "torrent_invalid", str(exc), "torrent")

    content_store = SharedContentStore(settings.data_root)
    payload_store = TorrentPayloadStore(
        settings.data_root,
        allowed_tracker_hosts=settings.c411_tracker_hosts,
        max_total_size=max_total,
    )
    try:
        total_bytes, free_bytes = await run_in_threadpool(content_store.disk_capacity)
    except SharedContentStoreError:
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "storage_unavailable",
            "Le stockage partagé est momentanément indisponible.",
        )

    staged_key: uuid.UUID | None = None
    job_created = False
    try:
        async with db.begin():
            result = await create_or_get_torrent_request(
                db,
                user_id=user_id,
                info_hash=parsed.info_hash,
                name=parsed.name,
                total_size=parsed.total_size,
                storage_policy=policy,
                disk_snapshot=StorageDiskSnapshot(total_bytes, free_bytes),
            )
            if result.request_created:
                active_count = await db.scalar(
                    select(func.count())
                    .select_from(TorrentRequest)
                    .where(
                        TorrentRequest.user_id == user_id,
                        TorrentRequest.state.in_(ACTIVE_REQUEST_STATES),
                    )
                )
                if active_count is None or active_count > max_active:
                    _fail(
                        status.HTTP_409_CONFLICT,
                        "torrent_limit_reached",
                        "Tu as atteint le nombre maximal de téléchargements actifs.",
                    )
            if result.managed_torrent_created:
                staged_key = result.managed_torrent.storage_key
                staged = await run_in_threadpool(
                    payload_store.stage,
                    parsed.content,
                    storage_key=staged_key,
                    max_total_size=max_total,
                )
                if staged.info_hash != parsed.info_hash:
                    raise TorrentPayloadStoreError("staged torrent hash changed")
                db.add(
                    TorrentJob(
                        managed_torrent_id=result.managed_torrent.id,
                        torrent_request_id=result.request.id,
                        job_type=ADD_TORRENT_JOB,
                        idempotency_key=f"add:{result.managed_torrent.id}",
                        state=TorrentJobState.QUEUED,
                        available_at=datetime.now(UTC),
                    )
                )
                await db.flush()
                job_created = True
    except HTTPException:
        if staged_key is not None:
            await run_in_threadpool(payload_store.remove, staged_key)
        raise
    except StorageAdmissionError as exc:
        code = exc.code
        message = {
            "user_quota_exceeded": "Ton quota de stockage est dépassé.",
            "managed_quota_exceeded": "La capacité de stockage gérée est atteinte.",
            "disk_pressure_critical": "Le stockage est temporairement sous forte pression.",
        }.get(code, "Le stockage ne peut pas accepter ce torrent.")
        _fail(status.HTTP_507_INSUFFICIENT_STORAGE, code, message)
    except TorrentMetadataConflictError:
        _fail(
            status.HTTP_409_CONFLICT,
            "torrent_metadata_conflict",
            "Les métadonnées de ce torrent sont incompatibles avec la copie existante.",
        )
    except TorrentDeduplicationError:
        _fail(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "torrent_request_invalid",
            "La demande de torrent est invalide.",
        )
    except (TorrentPayloadStoreError, IntegrityError, SQLAlchemyError):
        await db.rollback()
        if staged_key is not None:
            await run_in_threadpool(payload_store.remove, staged_key)
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "torrent_request_unavailable",
            "La demande n’a pas pu être enregistrée. Réessaie dans quelques instants.",
        )

    if job_created:
        await redis.signal_job_available()
    base = _response(result.request, result.managed_torrent)
    return TorrentRequestV2CreateResponse(
        **base.model_dump(),
        created=result.request_created,
        storage_pressure=cast(PressureState, result.storage_pressure.value.lower()),
    )


@router.get("", response_model=TorrentRequestV2ListingResponse)
async def list_torrent_requests(
    db: DbSession,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 25,
) -> TorrentRequestV2ListingResponse:
    total = await db.scalar(
        select(func.count())
        .select_from(TorrentRequest)
        .where(TorrentRequest.user_id == context.user.id)
    )
    rows = (
        await db.execute(
            select(TorrentRequest, ManagedTorrent)
            .join(ManagedTorrent, ManagedTorrent.id == TorrentRequest.managed_torrent_id)
            .where(TorrentRequest.user_id == context.user.id)
            .order_by(TorrentRequest.created_at.desc(), TorrentRequest.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return TorrentRequestV2ListingResponse(
        items=[_response(request, managed) for request, managed in rows],
        offset=offset,
        limit=limit,
        total=total or 0,
    )
