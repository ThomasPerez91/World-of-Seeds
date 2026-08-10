from fastapi import APIRouter

from app.api.routes import admin, auth, health

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(admin.router, prefix="/admin", tags=["administration"])
api_router.include_router(health.router, prefix="/health", tags=["health"])
