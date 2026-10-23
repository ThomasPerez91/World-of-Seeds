import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response as StarletteResponse

from app import __version__
from app.admin.disk_reads import ReconciliationBusyError, ReconciliationDiskReads
from app.api.external.errors import ExternalApiError
from app.api.external.middleware import ExternalRequestIdMiddleware
from app.api.external.rate_limit import ExternalApiRateLimiter
from app.api.router import api_v2_router, build_api_router, external_v1_router
from app.auth.passwords import PasswordWorkUnavailableError, password_work_pool
from app.auth.rate_limit import LoginIpRateLimiter
from app.coordination import RedisCoordinator
from app.core.config import Settings, get_settings
from app.core.database import engine
from app.core.database_errors import DatabaseUnavailableError
from app.core.drain import RequestDrain, RequestDrainMiddleware
from app.core.http_security import SecurityHeadersMiddleware
from app.integrations import ExternalServicesMonitor
from app.integrations.admin_runtime import AdminRuntimeMonitor
from app.integrations.network_collection import NetworkThroughputCollector
from app.integrations.newgreedy_config import NewGreedyConfigStore
from app.integrations.newgreedy_restart import NewGreedyRestartStore
from app.integrations.prometheus_network import PrometheusNetworkError
from app.integrations.wos_restart import WosRestartStore
from app.observability import MetricsRegistry, OperationalMetricsCache, RequestMetricsMiddleware
from app.options import OptionsStore
from app.torrents.downloads import DownloadRateLimiter
from app.torrents.retry_reads import TorrentRetryReads
from app.torrents.traffic import DownloadTrafficScheduler
from app.torrents.validation_work import TorrentValidationBusyError, torrent_validation_pool

logger = logging.getLogger(__name__)


async def monitor_http_upload(application: FastAPI) -> None:
    """Re-evaluate HTTP stream capacity from the existing host network metric."""
    settings: Settings = application.state.settings
    if settings.prometheus_url is None:
        return
    prometheus: NetworkThroughputCollector = application.state.network_throughput_collector
    while True:
        try:
            snapshot = await prometheus.snapshot("realtime")
            if snapshot.status == "ok" and snapshot.upload is not None and snapshot.upload.samples:
                await application.state.download_traffic_scheduler.observe_upload(
                    snapshot.upload.current_bytes_per_second,
                    sample_age_seconds=max(
                        0,
                        (datetime.now(UTC) - snapshot.upload.samples[-1].timestamp).total_seconds(),
                    ),
                )
        except PrometheusNetworkError:
            logger.warning("HTTP upload scheduler could not read network telemetry")
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    password_work_pool.start()
    torrent_validation_pool.start()
    monitor = asyncio.create_task(monitor_http_upload(application))
    try:
        yield
    finally:
        application.state.request_drain.begin()
        if not await application.state.request_drain.wait_closed():
            logger.warning("API request cleanup exceeded its shutdown budget")
        if not await application.state.torrent_retry_reads.wait_idle():
            logger.warning("torrent_retry_reads_shutdown_timeout")
        if not await application.state.reconciliation_disk_reads.wait_idle():
            logger.warning("reconciliation_disk_cleanup_budget_exceeded")
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor
        await application.state.network_throughput_collector.aclose()
        await password_work_pool.aclose()
        await torrent_validation_pool.aclose()
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
    application.state.settings = settings
    application.state.reconciliation_disk_reads = ReconciliationDiskReads()
    application.state.torrent_retry_reads = TorrentRetryReads()
    application.state.network_throughput_collector = NetworkThroughputCollector(settings)
    application.state.request_drain = RequestDrain()
    application.state.external_services_monitor = ExternalServicesMonitor(settings)
    application.state.admin_runtime_monitor = AdminRuntimeMonitor(settings)
    application.state.redis_coordinator = RedisCoordinator.from_settings(settings)
    application.state.newgreedy_config_store = NewGreedyConfigStore(
        settings.data_root,
        max_bytes=settings.newgreedy_config_max_bytes,
    )
    application.state.newgreedy_restart_store = NewGreedyRestartStore(settings.data_root)
    application.state.wos_restart_store = WosRestartStore(settings.data_root)
    application.state.options_store = OptionsStore(settings.data_root)
    application.state.download_rate_limiter = DownloadRateLimiter()
    application.state.download_traffic_scheduler = DownloadTrafficScheduler(
        uplink_bytes_per_second=settings.http_uplink_capacity_bytes_per_second
    )
    application.state.external_api_rate_limiter = ExternalApiRateLimiter()
    application.state.login_ip_rate_limiter = LoginIpRateLimiter(
        maximum=settings.auth_ip_max_attempts,
        window_seconds=settings.auth_ip_window_seconds,
        fallback_maximum=settings.auth_max_attempts,
    )
    application.state.password_reset_ip_rate_limiter = LoginIpRateLimiter(
        maximum=settings.auth_ip_max_attempts,
        window_seconds=settings.auth_ip_window_seconds,
        fallback_maximum=settings.auth_max_attempts,
    )
    application.state.metrics_registry = MetricsRegistry()
    application.state.operational_metrics_cache = OperationalMetricsCache()
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    application.add_middleware(RequestDrainMiddleware, drain=application.state.request_drain)
    application.add_middleware(ExternalRequestIdMiddleware)
    application.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=settings.cookie_secure,
    )
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

    async def database_error_handler(
        request: Request, exc: DatabaseUnavailableError | PoolTimeoutError
    ) -> JSONResponse:
        logger.warning("api_database_unavailable")
        if request.url.path.startswith("/api/external/v1"):
            response = await external_error_handler(
                request,
                ExternalApiError(
                    503, "database_unavailable", "Database temporarily unavailable", 5
                ),
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "database_unavailable",
                    "message": "Database temporarily unavailable",
                    "field": None,
                }
            },
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        )

    async def reconciliation_busy_handler(
        request: Request, exc: ReconciliationBusyError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "reconciliation_busy",
                    "message": "Une vérification disque est en cours. Réessaie dans un instant.",
                    "field": None,
                }
            },
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        )

    async def torrent_validation_busy_handler(
        request: Request, exc: TorrentValidationBusyError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "torrent_validation_busy",
                    "message": "Torrent validation is busy",
                    "field": None,
                }
            },
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )

    async def password_work_error_handler(
        request: Request, exc: PasswordWorkUnavailableError
    ) -> JSONResponse:
        if request.url.path.startswith("/api/external/v1"):
            return await external_error_handler(
                request,
                ExternalApiError(
                    503, "authentication_unavailable", "Authentication temporarily unavailable", 1
                ),
            )
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "authentication_unavailable",
                    "message": "Authentication temporarily unavailable",
                    "field": None,
                }
            },
            headers={"Retry-After": "1"},
        )

    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        if request.url.path == "/api/v1/auth/password-reset/confirm":
            return JSONResponse(
                status_code=422,
                content={
                    "detail": {
                        "code": "password_reset_unavailable",
                        "message": "Invalid recovery request",
                    }
                },
                headers={"Cache-Control": "no-store"},
            )
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

    application.add_exception_handler(ReconciliationBusyError, reconciliation_busy_handler)  # type: ignore[arg-type]
    application.add_exception_handler(DatabaseUnavailableError, database_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(PoolTimeoutError, database_error_handler)  # type: ignore[arg-type]
    application.add_exception_handler(TorrentValidationBusyError, torrent_validation_busy_handler)  # type: ignore[arg-type]
    application.add_exception_handler(PasswordWorkUnavailableError, password_work_error_handler)  # type: ignore[arg-type]
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
