from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response as StarletteResponse

from app import __version__
from app.api.external.errors import ExternalApiError
from app.api.external.middleware import ExternalRequestIdMiddleware
from app.api.external.rate_limit import ExternalApiRateLimiter
from app.api.router import api_v2_router, build_api_router, external_v1_router
from app.coordination import RedisCoordinator
from app.core.archive_queue import ArchiveDownloadQueueMiddleware
from app.core.config import Settings, get_settings
from app.core.database import engine
from app.core.http_security import SecurityHeadersMiddleware
from app.integrations import ExternalServicesMonitor
from app.integrations.newgreedy_config import NewGreedyConfigStore
from app.integrations.newgreedy_restart import NewGreedyRestartStore
from app.integrations.wos_restart import WosRestartStore
from app.observability import MetricsRegistry, OperationalMetricsCache, RequestMetricsMiddleware
from app.options import OptionsStore
from app.torrents.downloads import DownloadRateLimiter


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    yield
    await application.state.redis_coordinator.aclose()
    await engine.dispose()


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override if settings_override is not None else get_settings()
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
    application.state.external_services_monitor = ExternalServicesMonitor(settings)
    application.state.redis_coordinator = RedisCoordinator.from_settings(settings)
    application.state.newgreedy_config_store = NewGreedyConfigStore(
        settings.data_root,
        max_bytes=settings.newgreedy_config_max_bytes,
    )
    application.state.newgreedy_restart_store = NewGreedyRestartStore(settings.data_root)
    application.state.wos_restart_store = WosRestartStore(settings.data_root)
    application.state.options_store = OptionsStore(settings.data_root)
    application.state.download_rate_limiter = DownloadRateLimiter()
    application.state.external_api_rate_limiter = ExternalApiRateLimiter()
    application.state.metrics_registry = MetricsRegistry()
    application.state.operational_metrics_cache = OperationalMetricsCache()
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    application.add_middleware(ExternalRequestIdMiddleware)
    application.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=settings.cookie_secure,
    )
    application.add_middleware(ArchiveDownloadQueueMiddleware)
    application.add_middleware(
        RequestMetricsMiddleware,
        registry=application.state.metrics_registry,
    )
    application.include_router(
        build_api_router(runtime_profile=settings.runtime_profile),
        prefix="/api/v1",
    )
    application.include_router(api_v2_router, prefix="/api/v2")
    application.include_router(external_v1_router, prefix="/api/external/v1")

    async def external_error_handler(request: Request, exc: ExternalApiError) -> JSONResponse:
        request_id = getattr(request.state, "external_request_id", "unknown")
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request_id,
                }
            },
            headers=headers,
        )

    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if request.url.path.startswith("/api/external/v1"):
            request_id = getattr(request.state, "external_request_id", "unknown")
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "validation_error",
                        "message": "Request validation failed",
                        "request_id": request_id,
                    }
                },
            )
        from fastapi.exception_handlers import request_validation_exception_handler

        return await request_validation_exception_handler(request, exc)

    async def external_http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> StarletteResponse:
        if request.url.path.startswith("/api/external/v1"):
            request_id = getattr(request.state, "external_request_id", "unknown")
            code = "not_found" if exc.status_code == 404 else "method_not_allowed"
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error": {
                        "code": code,
                        "message": str(exc.detail),
                        "request_id": request_id,
                    }
                },
                headers=exc.headers,
            )
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

    application.add_exception_handler(ExternalApiError, external_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(StarletteHTTPException, external_http_error_handler)  # type: ignore[arg-type]

    if settings.static_root.is_dir():
        application.mount(
            "/",
            StaticFiles(directory=settings.static_root, html=True),
            name="frontend",
        )

    return application


app = create_app()
