from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AppSettings
from app.coordination.dependencies import RedisCoordinatorDependency
from app.core.database import get_db_session
from app.integrations.dependencies import ExternalServicesMonitorDependency
from app.integrations.observability_v2 import load_v2_external_services_snapshot
from app.schemas.health import HealthResponse, PublicSystemHealthResponse

router = APIRouter()


@router.get("/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    """Confirm that the application process can answer requests."""
    return HealthResponse(status="ok")


@router.get("/ready", response_model=HealthResponse)
async def readiness(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> HealthResponse:
    """Confirm that the application can reach PostgreSQL."""
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable",
        ) from exc
    return HealthResponse(status="ok")


@router.get("/status", response_model=PublicSystemHealthResponse)
async def system_status(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    monitor: ExternalServicesMonitorDependency,
    redis: RedisCoordinatorDependency,
    settings: AppSettings,
) -> PublicSystemHealthResponse:
    database_healthy = True
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        database_healthy = False
    finally:
        # Release the pool connection before potentially slow integration probes.
        await session.rollback()

    redis_health = await redis.check_health()
    snapshot = (
        await load_v2_external_services_snapshot(session)
        if settings.runtime_profile == "v2"
        else await monitor.snapshot()
    )
    return PublicSystemHealthResponse(
        status=(
            "ok"
            if database_healthy and snapshot.healthy and redis_health.permits_requests
            else "degraded"
        ),
        checked_at=snapshot.checked_at,
    )
