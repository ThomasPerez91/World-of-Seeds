from fastapi import APIRouter

from app.api.routes import (
    admin,
    admin_v2,
    auth,
    files,
    health,
    metrics_v2,
    torrents,
    torrents_v2,
    trash,
)


def build_api_router(*, runtime_profile: str) -> APIRouter:
    router = APIRouter()
    router.include_router(auth.router, prefix="/auth", tags=["authentication"])
    router.include_router(admin.router, prefix="/admin", tags=["administration"])
    router.include_router(files.router, prefix="/files", tags=["files"])
    router.include_router(health.router, prefix="/health", tags=["health"])
    if runtime_profile == "v1":
        router.include_router(torrents.router, prefix="/torrents", tags=["torrents"])
    router.include_router(trash.router, prefix="/trash", tags=["trash"])
    return router


# Compatibility export for imports that still assemble the V1 application router directly.
api_router = build_api_router(runtime_profile="v1")

api_v2_router = APIRouter()
api_v2_router.include_router(admin_v2.router, prefix="/admin", tags=["administration-v2"])
api_v2_router.include_router(metrics_v2.router, prefix="/metrics", tags=["metrics-v2"])
api_v2_router.include_router(torrents_v2.router, prefix="/torrents", tags=["torrents-v2"])
