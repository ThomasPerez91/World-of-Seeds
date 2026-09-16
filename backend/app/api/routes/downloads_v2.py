from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import AuthContext, DbSession, require_current_credentials
from app.options import DatabaseOptionsDriftError, PostgresOptionsRegistry
from app.schemas.torrents_v2 import TorrentDownloadPolicyResponse

router = APIRouter()


@router.get("/policy", response_model=TorrentDownloadPolicyResponse)
async def get_download_policy(
    db: DbSession,
    context: Annotated[AuthContext, Depends(require_current_credentials)],
) -> TorrentDownloadPolicyResponse:
    is_admin = context.user.is_admin
    try:
        options = await PostgresOptionsRegistry().snapshot(db)
        value = options.get("WOS_DOWNLOAD_MAX_CONCURRENT_PER_USER")
        global_value = options.get("WOS_DOWNLOAD_MAX_CONCURRENT_GLOBAL")
        if type(value) is not int or not 1 <= value <= 20:
            raise DatabaseOptionsDriftError("download concurrency option is invalid")
        if type(global_value) is not int or not 1 <= global_value <= 20:
            raise DatabaseOptionsDriftError("global download concurrency option is invalid")
    except (DatabaseOptionsDriftError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "download_options_unavailable",
                "message": "La configuration des téléchargements est momentanément indisponible.",
                "field": None,
            },
        ) from exc
    await db.rollback()
    if is_admin:
        return TorrentDownloadPolicyResponse(max_concurrent_streams=global_value, unlimited=True)
    return TorrentDownloadPolicyResponse(
        max_concurrent_streams=min(value, global_value),
        unlimited=False,
    )
