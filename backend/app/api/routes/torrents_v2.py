from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal, Never, cast

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
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
from app.files.downloads import (
    ByteRange,
    RangeNotSatisfiableError,
    if_range_matches,
    parse_range_header,
)
from app.jobs.torrent_effects import ADD_TORRENT_JOB
from app.jobs.torrent_payloads import TorrentPayloadStore, TorrentPayloadStoreError
from app.models import (
    ManagedTorrent,
    ManagedTorrentState,
    TorrentFile,
    TorrentJob,
    TorrentJobState,
    TorrentRequest,
    TorrentRequestState,
)
from app.options import DatabaseOptionsDriftError, PostgresOptionsRegistry
from app.schemas.torrents_v2 import (
    TorrentDownloadFileResponse,
    TorrentDownloadManifestResponse,
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
from app.torrents.downloads import (
    DownloadConcurrencyError,
    DownloadLeaseManager,
    DownloadRateLimiter,
    ManagedArchiveBusyError,
    ManagedArchiveEntry,
    ManagedArchiveStreamingResponse,
    ManagedDownloadError,
    ManagedDownloadStreamingResponse,
    ManagedFileDownloader,
    ManagedFolderArchiver,
    download_snapshot_id,
)

router = APIRouter()
UPLOAD_READ_CHUNK = 64 * 1024
MAX_PAGE_SIZE = 100
MAX_MANIFEST_PAGE_SIZE = 500
MAX_MANAGED_ARCHIVE_ENTRIES = 50_000
ACTIVE_REQUEST_STATES = (
    TorrentRequestState.REQUESTED,
    TorrentRequestState.ACTIVE,
    TorrentRequestState.READY,
)
RequestState = Literal["requested", "active", "ready", "cancelled", "expired", "error"]
PressureState = Literal["normal", "warning", "critical"]


def _download_rate_limiter(request: Request) -> DownloadRateLimiter:
    limiter = request.app.state.download_rate_limiter
    if not isinstance(limiter, DownloadRateLimiter):
        raise RuntimeError("download rate limiter is unavailable")
    return limiter


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


@router.get(
    "/{torrent_request_id}/download-manifest",
    response_model=TorrentDownloadManifestResponse,
)
async def get_torrent_download_manifest(
    db: DbSession,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
    torrent_request_id: uuid.UUID,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_MANIFEST_PAGE_SIZE)] = MAX_MANIFEST_PAGE_SIZE,
    snapshot: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> TorrentDownloadManifestResponse:
    row = (
        await db.execute(
            select(TorrentRequest, ManagedTorrent)
            .join(ManagedTorrent, ManagedTorrent.id == TorrentRequest.managed_torrent_id)
            .where(
                TorrentRequest.id == torrent_request_id,
                TorrentRequest.user_id == context.user.id,
                TorrentRequest.state == TorrentRequestState.READY,
                ManagedTorrent.state == ManagedTorrentState.READY,
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        await db.rollback()
        _fail(
            status.HTTP_404_NOT_FOUND, "torrent_manifest_not_found", "Ce contenu est indisponible."
        )
    torrent_request, managed_torrent = row
    checksum = managed_torrent.manifest_checksum
    if managed_torrent.manifest_version < 1 or checksum is None:
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "torrent_manifest_unavailable",
            "Le manifeste du téléchargement est indisponible.",
        )
    snapshot_id = download_snapshot_id(
        torrent_request.id,
        checksum,
        managed_torrent.manifest_version,
    )
    if snapshot is not None and snapshot != snapshot_id:
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "download_snapshot_changed",
            "Le contenu a changé. Relance le téléchargement.",
        )
    try:
        options = await PostgresOptionsRegistry().snapshot(db)
        archive_max_bytes = _integer_option(options, "WOS_FOLDER_ARCHIVE_MAX_BYTES")
    except (DatabaseOptionsDriftError, ValueError):
        await db.rollback()
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "download_options_unavailable",
            "Le téléchargement est momentanément indisponible.",
        )
    files = tuple(
        (
            await db.scalars(
                select(TorrentFile)
                .where(TorrentFile.managed_torrent_id == managed_torrent.id)
                .order_by(TorrentFile.file_index)
                .offset(offset)
                .limit(limit)
            )
        ).all()
    )
    expected_count = min(limit, max(0, managed_torrent.manifest_file_count - offset))
    if len(files) != expected_count:
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "download_snapshot_changed",
            "Le contenu a changé. Relance le téléchargement.",
        )
    response = TorrentDownloadManifestResponse(
        snapshot_id=snapshot_id,
        manifest_version=managed_torrent.manifest_version,
        file_count=managed_torrent.manifest_file_count,
        total_size=managed_torrent.manifest_total_size,
        archive_available=(
            managed_torrent.manifest_total_size <= archive_max_bytes
            and managed_torrent.manifest_file_count <= MAX_MANAGED_ARCHIVE_ENTRIES
        ),
        offset=offset,
        limit=limit,
        items=[
            TorrentDownloadFileResponse(
                id=item.id,
                file_index=item.file_index,
                relative_path=item.relative_path,
                size=item.size,
            )
            for item in files
        ],
    )
    await db.rollback()
    return response


@router.get(
    "/{torrent_request_id}/download-archive",
    response_model=None,
    operation_id="download_v2_torrent_archive",
)
async def download_torrent_archive(
    request: Request,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[AuthContext, Depends(require_current_credentials)],
    torrent_request_id: uuid.UUID,
    snapshot: Annotated[str, Query(min_length=64, max_length=64)],
) -> Response:
    owner_id = context.user.id
    row = (
        await db.execute(
            select(TorrentRequest, ManagedTorrent)
            .join(ManagedTorrent, ManagedTorrent.id == TorrentRequest.managed_torrent_id)
            .where(
                TorrentRequest.id == torrent_request_id,
                TorrentRequest.user_id == owner_id,
                TorrentRequest.state == TorrentRequestState.READY,
                ManagedTorrent.state == ManagedTorrentState.READY,
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        await db.rollback()
        _fail(
            status.HTTP_404_NOT_FOUND, "torrent_archive_not_found", "Ce contenu est indisponible."
        )
    torrent_request, managed_torrent = row
    checksum = managed_torrent.manifest_checksum
    if managed_torrent.manifest_version < 1 or checksum is None:
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "torrent_manifest_unavailable",
            "Le manifeste du téléchargement est indisponible.",
        )
    if snapshot != download_snapshot_id(
        torrent_request.id,
        checksum,
        managed_torrent.manifest_version,
    ):
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "download_snapshot_changed",
            "Le contenu a changé. Relance le téléchargement.",
        )
    try:
        options = await PostgresOptionsRegistry().snapshot(db)
        archive_max_bytes = _integer_option(options, "WOS_FOLDER_ARCHIVE_MAX_BYTES")
        chunk_size = _integer_option(options, "WOS_HTTP_STREAM_CHUNK_BYTES")
        lease_seconds = _integer_option(options, "WOS_DOWNLOAD_LEASE_SECONDS")
        max_concurrent = _integer_option(options, "WOS_DOWNLOAD_MAX_CONCURRENT_PER_USER")
        per_user_rate = _integer_option(
            options,
            "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER",
        )
        global_rate = _integer_option(options, "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL")
    except (DatabaseOptionsDriftError, ValueError):
        await db.rollback()
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "download_options_unavailable",
            "Le téléchargement est momentanément indisponible.",
        )
    if (
        managed_torrent.manifest_total_size > archive_max_bytes
        or managed_torrent.manifest_file_count > MAX_MANAGED_ARCHIVE_ENTRIES
    ):
        await db.rollback()
        _fail(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "torrent_archive_too_large",
            "Ce contenu est trop volumineux pour le fallback ZIP.",
        )
    files = tuple(
        (
            await db.scalars(
                select(TorrentFile)
                .where(TorrentFile.managed_torrent_id == managed_torrent.id)
                .order_by(TorrentFile.file_index)
            )
        ).all()
    )
    if len(files) != managed_torrent.manifest_file_count or not files:
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "download_snapshot_changed",
            "Le contenu a changé. Relance le téléchargement.",
        )
    managed_torrent_id = managed_torrent.id
    storage_key = managed_torrent.storage_key
    manifest_version = managed_torrent.manifest_version
    download_name = f"{managed_torrent.name}.zip"
    first_file_id = files[0].id
    entries = tuple(
        ManagedArchiveEntry(item.relative_path, item.size, item.file_index) for item in files
    )
    await db.rollback()

    leases = DownloadLeaseManager(db, lease_seconds=lease_seconds)
    try:
        lease = await leases.acquire(
            user_id=owner_id,
            managed_torrent_id=managed_torrent_id,
            torrent_request_id=torrent_request_id,
            torrent_file_id=first_file_id,
            max_concurrent=max_concurrent,
        )
    except DownloadConcurrencyError:
        _fail(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "download_concurrency_reached",
            "Trop de téléchargements sont déjà actifs.",
        )
    archiver = ManagedFolderArchiver(
        ManagedFileDownloader(SharedContentStore(settings.data_root)),
        storage_key=storage_key,
        entries=entries,
        manifest_checksum=checksum,
        manifest_version=manifest_version,
        download_name=download_name,
    )
    try:
        archiver.acquire()
    except ManagedArchiveBusyError:
        await leases.release(lease.id)
        _fail(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "torrent_archive_busy",
            "Un autre fallback ZIP est déjà en cours.",
        )
    return ManagedArchiveStreamingResponse(
        archiver,
        chunk_size=chunk_size,
        user_id=owner_id,
        lease=lease,
        leases=leases,
        limiter=_download_rate_limiter(request),
        per_user_bytes_per_second=per_user_rate,
        global_bytes_per_second=global_rate,
    )


@router.get(
    "/{torrent_request_id}/files/{torrent_file_id}/download",
    response_model=None,
    operation_id="download_v2_torrent_file",
)
@router.head(
    "/{torrent_request_id}/files/{torrent_file_id}/download",
    response_model=None,
    operation_id="head_v2_torrent_file",
)
async def download_torrent_file(
    request: Request,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[AuthContext, Depends(require_current_credentials)],
    torrent_request_id: uuid.UUID,
    torrent_file_id: uuid.UUID,
    download_snapshot: Annotated[str | None, Header(alias="X-WOS-Download-Snapshot")] = None,
    snapshot: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> Response:
    owner_id = context.user.id
    row = (
        await db.execute(
            select(TorrentRequest, ManagedTorrent, TorrentFile)
            .join(ManagedTorrent, ManagedTorrent.id == TorrentRequest.managed_torrent_id)
            .join(TorrentFile, TorrentFile.managed_torrent_id == ManagedTorrent.id)
            .where(
                TorrentRequest.id == torrent_request_id,
                TorrentRequest.user_id == owner_id,
                TorrentRequest.state == TorrentRequestState.READY,
                ManagedTorrent.state == ManagedTorrentState.READY,
                TorrentFile.id == torrent_file_id,
            )
        )
    ).one_or_none()
    if row is None:
        await db.rollback()
        _fail(status.HTTP_404_NOT_FOUND, "torrent_file_not_found", "Ce fichier est indisponible.")
    torrent_request, managed_torrent, torrent_file = row
    if managed_torrent.manifest_version < 1 or managed_torrent.manifest_checksum is None:
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "torrent_manifest_unavailable",
            "Le manifeste du téléchargement est indisponible.",
        )
    managed_torrent_id = managed_torrent.id
    torrent_request_id = torrent_request.id
    torrent_file_id = torrent_file.id
    storage_key = managed_torrent.storage_key
    relative_path = torrent_file.relative_path
    file_size = torrent_file.size
    file_index = torrent_file.file_index
    manifest_checksum = managed_torrent.manifest_checksum
    manifest_version = managed_torrent.manifest_version
    if download_snapshot is not None and snapshot is not None and download_snapshot != snapshot:
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "download_snapshot_changed",
            "Le contenu a changé. Relance le téléchargement.",
        )
    expected_snapshot = download_snapshot or snapshot
    if expected_snapshot is not None and expected_snapshot != download_snapshot_id(
        torrent_request_id,
        manifest_checksum,
        manifest_version,
    ):
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "download_snapshot_changed",
            "Le contenu a changé. Relance le téléchargement.",
        )
    try:
        options = await PostgresOptionsRegistry().snapshot(db)
        chunk_size = _integer_option(options, "WOS_HTTP_STREAM_CHUNK_BYTES")
        lease_seconds = _integer_option(options, "WOS_DOWNLOAD_LEASE_SECONDS")
        max_concurrent = _integer_option(options, "WOS_DOWNLOAD_MAX_CONCURRENT_PER_USER")
        per_user_rate = _integer_option(
            options,
            "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER",
        )
        global_rate = _integer_option(options, "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL")
    except (DatabaseOptionsDriftError, ValueError):
        await db.rollback()
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "download_options_unavailable",
            "Le téléchargement est momentanément indisponible.",
        )
    await db.rollback()

    leases = DownloadLeaseManager(db, lease_seconds=lease_seconds)
    lease = None
    if request.method != "HEAD":
        try:
            lease = await leases.acquire(
                user_id=owner_id,
                managed_torrent_id=managed_torrent_id,
                torrent_request_id=torrent_request_id,
                torrent_file_id=torrent_file_id,
                max_concurrent=max_concurrent,
            )
        except DownloadConcurrencyError:
            _fail(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "download_concurrency_reached",
                "Trop de téléchargements sont déjà actifs.",
            )

    try:
        download = await run_in_threadpool(
            ManagedFileDownloader(SharedContentStore(settings.data_root)).open,
            storage_key,
            relative_path,
            expected_size=file_size,
            manifest_checksum=manifest_checksum,
            manifest_version=manifest_version,
            file_index=file_index,
        )
    except (ManagedDownloadError, SharedContentStoreError):
        if lease is not None:
            await leases.release(lease.id)
        _fail(
            status.HTTP_409_CONFLICT,
            "torrent_file_changed",
            "Le fichier ne correspond plus au manifeste.",
        )

    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-cache",
        "Content-Disposition": download.content_disposition,
        "Content-Type": download.media_type,
        "ETag": download.etag,
        "Last-Modified": download.last_modified,
        "X-WOS-Manifest-Version": str(manifest_version),
    }
    selected_range: ByteRange | None = None
    range_header = request.headers.get("range")
    if range_header is not None and if_range_matches(request.headers.get("if-range"), download):
        try:
            selected_range = parse_range_header(range_header, download.size)
        except RangeNotSatisfiableError:
            download.close()
            if lease is not None:
                await leases.release(lease.id)
            headers.update({"Content-Length": "0", "Content-Range": f"bytes */{download.size}"})
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                headers=headers,
            )

    if selected_range is None:
        response_status = status.HTTP_200_OK
        start = 0
        length = download.size
    else:
        response_status = status.HTTP_206_PARTIAL_CONTENT
        start = selected_range.start
        length = selected_range.length
        headers["Content-Range"] = (
            f"bytes {selected_range.start}-{selected_range.end}/{download.size}"
        )
    headers["Content-Length"] = str(length)
    if request.method == "HEAD":
        download.close()
        return Response(status_code=response_status, headers=headers)
    assert lease is not None
    return ManagedDownloadStreamingResponse(
        download,
        start=start,
        length=length,
        status_code=response_status,
        headers=headers,
        chunk_size=chunk_size,
        user_id=owner_id,
        lease=lease,
        leases=leases,
        limiter=_download_rate_limiter(request),
        per_user_bytes_per_second=per_user_rate,
        global_bytes_per_second=global_rate,
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
