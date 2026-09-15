from fastapi import APIRouter

from app.api.external.downloads import router as downloads_router
from app.api.external.schemas import ExternalHealthResponse
from app.api.external.users import router as users_router

router = APIRouter(tags=["External API v1"])


@router.get("/health", response_model=ExternalHealthResponse)
async def health() -> ExternalHealthResponse:
    return ExternalHealthResponse()


router.include_router(users_router)
router.include_router(downloads_router)
