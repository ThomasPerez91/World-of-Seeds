from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import AuthContext, require_current_credentials
from app.core.config import Settings, get_settings
from app.storage import SharedContentStore, SharedContentStoreError

router = APIRouter()


class SharedStorageCapacityResponse(BaseModel):
    total_bytes: int
    used_bytes: int
    available_bytes: int


@router.get("", response_model=SharedStorageCapacityResponse)
async def get_shared_storage_capacity(
    settings: Annotated[Settings, Depends(get_settings)],
    _context: Annotated[AuthContext, Depends(require_current_credentials)],
) -> SharedStorageCapacityResponse:
    store = SharedContentStore(settings.data_root)
    try:
        total, available = await run_in_threadpool(store.disk_capacity)
    except (OSError, SharedContentStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "storage_unavailable",
                "message": "Le stockage partagé est momentanément indisponible.",
                "field": None,
            },
        ) from exc
    return SharedStorageCapacityResponse(
        total_bytes=total,
        used_bytes=max(total - available, 0),
        available_bytes=available,
    )
