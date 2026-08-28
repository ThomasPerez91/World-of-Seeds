import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.coordination.dependencies import RedisCoordinatorDependency
from app.core.database import get_db_session
from app.integrations.dependencies import ExternalServicesMonitorDependency
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
) -> PublicSystemHealthResponse:
    database_healthy = True
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        database_healthy = False
    finally:
        # Release the pool connection before potentially slow integration probes.
        await session.rollback()

    snapshot, redis_health = await asyncio.gather(
        monitor.snapshot(),
        redis.check_health(),
    )
    return PublicSystemHealthResponse(
        status=(
            "ok"
            if database_healthy and snapshot.healthy and redis_health.permits_requests
            else "degraded"
        ),
        checked_at=snapshot.checked_at,
    )
