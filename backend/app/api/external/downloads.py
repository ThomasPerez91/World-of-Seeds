from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.external.dependencies import require_scope, resolve_external_user
from app.api.external.schemas import (
    ExternalDownloadResponse,
    ExternalDownloadsPage,
    ExternalErrorResponse,
)
from app.auth.dependencies import DbSession
from app.models import (
    ExternalApiClient,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.scheduler.queue_visibility import torrent_queue_status

router = APIRouter()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _required_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _state(
    request: TorrentRequest, torrent: ManagedTorrent, now: datetime
) -> Literal[
    "waiting", "downloading", "stalled", "cooldown", "ready", "error", "cancelled", "expired"
]:
    if torrent.state is ManagedTorrentState.ERROR:
        return "error"
    if request.state is TorrentRequestState.READY:
        return "ready"
    if request.state is TorrentRequestState.CANCELLED:
        return "cancelled"
    if request.state is TorrentRequestState.EXPIRED:
        return "expired"
    return torrent_queue_status(torrent, now=now) or "waiting"


@router.get(
    "/me/downloads",
    response_model=ExternalDownloadsPage,
    responses={
        401: {"model": ExternalErrorResponse},
        403: {"model": ExternalErrorResponse},
        422: {"model": ExternalErrorResponse},
        429: {"model": ExternalErrorResponse},
    },
    summary="List downloads for the user resolved from X-WOS-User-Seed",
)
async def list_external_downloads(
    db: DbSession,
    _client: Annotated[ExternalApiClient, Depends(require_scope("downloads:read"))],
    user: Annotated[User, Depends(resolve_external_user)],
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ExternalDownloadsPage:
    total = int(
        await db.scalar(
            select(func.count())
            .select_from(TorrentRequest)
            .where(TorrentRequest.user_id == user.id)
        )
        or 0
    )
    rows = (
        await db.execute(
            select(TorrentRequest, ManagedTorrent)
            .join(ManagedTorrent, ManagedTorrent.id == TorrentRequest.managed_torrent_id)
            .where(TorrentRequest.user_id == user.id)
            .order_by(TorrentRequest.created_at.desc(), TorrentRequest.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    now = datetime.now(UTC)
    items: list[ExternalDownloadResponse] = []
    for request, torrent in rows:
        unsubscribe_at = _utc(request.unsubscribe_at)
        remaining = (
            max(0, int((unsubscribe_at - now).total_seconds()))
            if request.state is TorrentRequestState.READY and unsubscribe_at is not None
            else None
        )
        items.append(
            ExternalDownloadResponse(
                id=request.id,
                name=torrent.name,
                state=_state(request, torrent, now),
                progress_percent=round(torrent.progress * 100, 2),
                size_bytes=torrent.total_size,
                queue_position=None,
                created_at=request.created_at,
                updated_at=max(
                    _required_utc(request.updated_at), _required_utc(torrent.updated_at)
                ),
                ready_at=_utc(request.ready_at),
                unsubscribe_at=unsubscribe_at,
                eta_seconds=None,
                access_remaining_seconds=remaining,
            )
        )
    await db.commit()
    return ExternalDownloadsPage(items=items, offset=offset, limit=limit, total=total)
