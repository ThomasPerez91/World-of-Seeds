from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.router import api_router
from app.core.config import get_settings
from app.core.database import engine
from app.core.http_security import SecurityHeadersMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    docs_url = "/api/docs" if settings.expose_api_docs else None
    openapi_url = "/api/openapi.json" if settings.expose_api_docs else None

    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    application.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=settings.cookie_secure,
    )
    application.include_router(api_router, prefix="/api/v1")

    if settings.static_root.is_dir():
        application.mount(
            "/",
            StaticFiles(directory=settings.static_root, html=True),
            name="frontend",
        )

    return application


app = create_app()
