from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.external.dependencies import require_scope, resolve_external_user
from app.api.external.errors import ExternalApiError
from app.api.external.schemas import (
    DiscordUserId,
    ExternalDownloadNotice,
    ExternalDownloadNoticesPage,
    ExternalDownloadResponse,
    ExternalDownloadsPage,
    ExternalErrorResponse,
)
from app.auth.dependencies import DbSession
from app.models import (
    DiscordAccountLink,
    ExternalApiClient,
    ManagedTorrent,
    ManagedTorrentState,
    TorrentRequest,
    TorrentRequestState,
    User,
)
from app.scheduler.queue_visibility import load_torrent_queue_visibility, torrent_queue_status

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
    if request.state is TorrentRequestState.CANCELLED:
        return "cancelled"
    if request.state is TorrentRequestState.EXPIRED:
        return "expired"
    if torrent.state is ManagedTorrentState.ERROR:
        return "error"
    if request.state is TorrentRequestState.READY:
        end = _utc(request.unsubscribe_at)
        return "expired" if end is not None and end <= now else "ready"
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
    return await downloads_page(db, user, offset=offset, limit=limit)


async def downloads_page(
    db: DbSession, user: User, *, offset: int, limit: int
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
    visibility = await load_torrent_queue_visibility(db, [torrent for _, torrent in rows], now=now)
    for request, torrent in rows:
        threshold = warning_at(request)
        state = _state(request, torrent, now)
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
                state=state,
                progress_percent=round(torrent.progress * 100, 2),
                size_bytes=torrent.total_size,
                queue_position=(
                    visibility[torrent.id].position_estimate
                    if torrent.id in visibility and state == "waiting"
                    else None
                ),
                access_warning_at=threshold,
                access_expiring_soon=(
                    remaining is not None
                    and remaining > 0
                    and state == "ready"
                    and threshold is not None
                    and now >= threshold
                ),
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


def warning_at(request: TorrentRequest) -> datetime | None:
    ready, end = _utc(request.ready_at), _utc(request.unsubscribe_at)
    if ready is None or end is None or end <= ready:
        return None
    return ready + (end - ready) * (2 / 3)


@router.get("/users/discord/{discord_user_id}/downloads", response_model=ExternalDownloadsPage)
async def list_discord_downloads(
    discord_user_id: DiscordUserId,
    db: DbSession,
    _client: Annotated[ExternalApiClient, Depends(require_scope("downloads:read-discord"))],
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> ExternalDownloadsPage:
    user = await db.scalar(
        select(User)
        .join(DiscordAccountLink, DiscordAccountLink.user_id == User.id)
        .where(DiscordAccountLink.discord_user_id == discord_user_id)
    )
    if user is None or user.deleted_at is not None or user.is_admin:
        raise ExternalApiError(404, "discord_account_not_found", "Discord account not found")
    if not user.is_active:
        raise ExternalApiError(403, "user_disabled", "User account is disabled")
    return await downloads_page(db, user, offset=offset, limit=limit)


@router.get("/discord/download-notices", response_model=ExternalDownloadNoticesPage)
async def list_download_notices(
    db: DbSession,
    _client: Annotated[ExternalApiClient, Depends(require_scope("downloads:read-discord"))],
    after: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ExternalDownloadNoticesPage:
    # Stateless keyset scan: only live, usable personal rights, never another owner's deadline.
    now = datetime.now(UTC)
    database_now = now.replace(tzinfo=None) if db.get_bind().dialect.name == "sqlite" else now
    query = (
        select(TorrentRequest, ManagedTorrent, DiscordAccountLink.discord_user_id)
        .select_from(TorrentRequest)
        .join(ManagedTorrent, ManagedTorrent.id == TorrentRequest.managed_torrent_id)
        .join(User, User.id == TorrentRequest.user_id)
        .join(DiscordAccountLink, DiscordAccountLink.user_id == User.id)
        .where(
            TorrentRequest.state == TorrentRequestState.READY,
            ManagedTorrent.state == ManagedTorrentState.READY,
            TorrentRequest.ready_at.is_not(None),
            TorrentRequest.unsubscribe_at > database_now,
            User.is_active.is_(True),
            User.deleted_at.is_(None),
            User.is_admin.is_(False),
        )
    )
    if after is not None:
        query = query.where(TorrentRequest.id > after)
    rows = (await db.execute(query.order_by(TorrentRequest.id).limit(limit + 1))).all()
    items = [
        ExternalDownloadNotice(
            request_id=request.id,
            discord_user_id=member,
            name=torrent.name,
            ready_at=_required_utc(request.ready_at or now),
            unsubscribe_at=_required_utc(request.unsubscribe_at or now),
            warning_at=warning_at(request),
        )
        for request, torrent, member in rows[:limit]
    ]
    await db.commit()
    return ExternalDownloadNoticesPage(
        items=items, next_after=items[-1].request_id if len(rows) > limit else None
    )
