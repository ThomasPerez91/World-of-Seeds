from fastapi import APIRouter

from app.api.routes import admin, admin_v2, auth, files, health, torrents, torrents_v2, trash

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(admin.router, prefix="/admin", tags=["administration"])
api_router.include_router(files.router, prefix="/files", tags=["files"])
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(torrents.router, prefix="/torrents", tags=["torrents"])
api_router.include_router(trash.router, prefix="/trash", tags=["trash"])

api_v2_router = APIRouter()
api_v2_router.include_router(admin_v2.router, prefix="/admin", tags=["administration-v2"])
api_v2_router.include_router(torrents_v2.router, prefix="/torrents", tags=["torrents-v2"])
