from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.auth.dependencies import AuthContext, require_current_credentials
from app.schemas.torrents_v2 import TorrentDownloadPolicyResponse
from app.torrents.traffic import DownloadTrafficScheduler

router = APIRouter()


@router.get("/policy", response_model=TorrentDownloadPolicyResponse)
async def get_download_policy(
    context: Annotated[AuthContext, Depends(require_current_credentials)],
) -> TorrentDownloadPolicyResponse:
    # A browser queues its own jobs locally; the API has no global file-stream cap.
    return TorrentDownloadPolicyResponse(max_concurrent_streams=2, unlimited=False)


@router.get("/traffic")
async def get_download_traffic(
    request: Request,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
) -> dict[str, int]:
    scheduler = request.app.state.download_traffic_scheduler
    if not isinstance(scheduler, DownloadTrafficScheduler):
        raise RuntimeError("download traffic scheduler is unavailable")
    return await scheduler.status(context.user.id)
