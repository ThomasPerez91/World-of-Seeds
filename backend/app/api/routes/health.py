from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.schemas.health import HealthResponse

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
