from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, Never

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import (
    AuthContext,
    DbSession,
    require_current_credentials,
    require_current_credentials_csrf,
)
from app.core.config import Settings, get_settings
from app.files import WorkspaceError
from app.files.dependencies import WorkspaceManagerDependency
from app.integrations.dependencies import ExternalServicesMonitorDependency
from app.integrations.http import IntegrationAuthenticationError, IntegrationRequestError
from app.integrations.types import QBittorrentTorrent
from app.models import UserTorrent
from app.options.dependencies import OptionsStoreDependency
from app.schemas.torrents import (
    TorrentUploadResponse,
    UserTorrentListingResponse,
    UserTorrentResponse,
)
from app.torrents import TorrentValidationError, normalize_torrent

router = APIRouter()
UPLOAD_READ_CHUNK = 64 * 1024
UserTorrentState = Literal["adding", "pending", "downloading", "stalled", "completed", "error"]


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


def _state(torrent: QBittorrentTorrent) -> UserTorrentState:
    if torrent.state in {"error", "missingFiles", "unknown"}:
        return "error"
    if torrent.progress >= 1 or torrent.state in {"uploading", "stalledUP", "queuedUP", "pausedUP"}:
        return "completed"
    if torrent.state == "stalledDL":
        return "stalled"
    if torrent.state in {"downloading", "forcedDL", "metaDL", "forcedMetaDL"}:
        return "downloading"
    return "pending"


@router.post("", response_model=TorrentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_torrent(
    db: DbSession,
    monitor: ExternalServicesMonitorDependency,
    options_store: OptionsStoreDependency,
    workspace_manager: WorkspaceManagerDependency,
    settings: Annotated[Settings, Depends(get_settings)],
    context: Annotated[AuthContext, Depends(require_current_credentials_csrf)],
    torrent: Annotated[UploadFile, File(description="Fichier BitTorrent C411")],
) -> TorrentUploadResponse:
    if torrent.filename is None or not torrent.filename.lower().endswith(".torrent"):
        _fail(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "torrent_filename_invalid",
            "Sélectionne un fichier portant l’extension .torrent.",
            "torrent",
        )
    options = options_store.snapshot()
    max_upload = options["WOS_TORRENT_UPLOAD_MAX_FILE_BYTES"]
    max_total = options["WOS_TORRENT_MAX_SIZE_BYTES"]
    max_active = options["WOS_TORRENT_MAX_ACTIVE_PER_USER"]
    if type(max_upload) is not int or type(max_total) is not int or type(max_active) is not int:
        raise RuntimeError("Torrent options have invalid types")

    passkey = settings.c411_passkey
    if passkey is None:
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "c411_not_configured",
            "L’ajout de torrents est momentanément indisponible.",
        )
    content = await _read_upload(torrent, max_upload)
    try:
        parsed = normalize_torrent(
            content,
            passkey=passkey.get_secret_value(),
            allowed_tracker_hosts=settings.c411_tracker_hosts,
            max_total_size=max_total,
        )
    except TorrentValidationError as exc:
        _fail(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "torrent_invalid",
            str(exc),
            "torrent",
        )

    existing = await db.scalar(
        select(UserTorrent).where(
            UserTorrent.user_id == context.user.id,
            UserTorrent.info_hash == parsed.info_hash,
        )
    )
    if existing is not None:
        _fail(
            status.HTTP_409_CONFLICT,
            "torrent_already_added",
            "Ce torrent figure déjà dans tes téléchargements.",
        )
    user_hashes = list(
        (
            await db.scalars(
                select(UserTorrent.info_hash)
                .where(UserTorrent.user_id == context.user.id)
                .limit(1_000)
            )
        ).all()
    )
    if len(user_hashes) >= max_active:
        try:
            active_snapshots, _ = await monitor.qbittorrent_torrents_by_hashes(user_hashes)
        except (IntegrationAuthenticationError, IntegrationRequestError):
            _fail(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "qbittorrent_unavailable",
                "Le service de téléchargement est momentanément indisponible.",
            )
        active_count = sum(
            snapshot.progress < 1 and snapshot.state not in {"error", "missingFiles"}
            for snapshot in active_snapshots
        )
        if active_count >= max_active:
            _fail(
                status.HTTP_409_CONFLICT,
                "torrent_limit_reached",
                "Tu as atteint le nombre maximal de téléchargements actifs.",
            )

    save_path = str(settings.qbittorrent_data_root / context.user.username / "downloads")
    if not PurePosixPath(save_path).is_absolute():
        raise RuntimeError("qBittorrent data root must be absolute")
    try:
        await run_in_threadpool(workspace_manager.assert_ready, context.user.username)
    except WorkspaceError:
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "user_storage_unavailable",
            "Ton espace de téléchargement est momentanément indisponible.",
        )
    try:
        await monitor.add_qbittorrent_torrent(parsed.content, save_path=save_path)
    except IntegrationAuthenticationError:
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "qbittorrent_authentication_failed",
            "Le service de téléchargement est momentanément indisponible.",
        )
    except IntegrationRequestError:
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "qbittorrent_unavailable",
            "Le torrent n’a pas pu être ajouté. Réessaie dans quelques instants.",
        )

    association = UserTorrent(
        user_id=context.user.id,
        info_hash=parsed.info_hash,
        name=parsed.name,
    )
    db.add(association)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        _fail(
            status.HTTP_409_CONFLICT,
            "torrent_already_added",
            "Ce torrent figure déjà dans tes téléchargements.",
        )
    return TorrentUploadResponse(
        id=parsed.info_hash,
        name=parsed.name,
        total_size=parsed.total_size,
    )


@router.get("", response_model=UserTorrentListingResponse)
async def list_user_torrents(
    db: DbSession,
    monitor: ExternalServicesMonitorDependency,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
) -> UserTorrentListingResponse:
    associations = list(
        (
            await db.scalars(
                select(UserTorrent)
                .where(UserTorrent.user_id == context.user.id)
                .order_by(UserTorrent.created_at.desc())
                .limit(1_000)
            )
        ).all()
    )
    if not associations:
        return UserTorrentListingResponse(torrents=[])
    try:
        snapshots, _ = await monitor.qbittorrent_torrents_by_hashes(
            [association.info_hash for association in associations]
        )
    except (IntegrationAuthenticationError, IntegrationRequestError):
        _fail(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "qbittorrent_unavailable",
            "Le suivi des téléchargements est momentanément indisponible.",
        )
    by_hash = {snapshot.id: snapshot for snapshot in snapshots}
    result: list[UserTorrentResponse] = []
    for association in associations:
        snapshot = by_hash.get(association.info_hash)
        if snapshot is None:
            created_at = association.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            missing_is_error = (datetime.now(UTC) - created_at).total_seconds() > 120
            result.append(
                UserTorrentResponse(
                    id=association.info_hash,
                    name=association.name,
                    size_bytes=0,
                    progress=0,
                    state="error" if missing_is_error else "adding",
                    downloaded_bytes=0,
                    download_speed_bytes=0,
                    eta_seconds=None,
                    error=(
                        "Le téléchargement est introuvable dans le service."
                        if missing_is_error
                        else None
                    ),
                    created_at=association.created_at,
                )
            )
            continue
        state = _state(snapshot)
        result.append(
            UserTorrentResponse(
                id=association.info_hash,
                name=snapshot.name,
                size_bytes=snapshot.size_bytes,
                progress=snapshot.progress,
                state=state,
                downloaded_bytes=snapshot.downloaded_bytes,
                download_speed_bytes=snapshot.download_speed_bytes,
                eta_seconds=snapshot.eta_seconds,
                error="Le téléchargement nécessite une intervention." if state == "error" else None,
                created_at=association.created_at,
            )
        )
    return UserTorrentListingResponse(torrents=result)
