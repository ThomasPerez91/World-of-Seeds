from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.router import api_router, api_v2_router
from app.coordination import RedisCoordinator
from app.core.config import get_settings
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
    application.state.metrics_registry = MetricsRegistry()
    application.state.operational_metrics_cache = OperationalMetricsCache()
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    application.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=settings.cookie_secure,
    )
    application.add_middleware(
        RequestMetricsMiddleware,
        registry=application.state.metrics_registry,
    )
    application.include_router(api_router, prefix="/api/v1")
    application.include_router(api_v2_router, prefix="/api/v2")

    if settings.static_root.is_dir():
        application.mount(
            "/",
            StaticFiles(directory=settings.static_root, html=True),
            name="frontend",
        )

    return application


app = create_app()
