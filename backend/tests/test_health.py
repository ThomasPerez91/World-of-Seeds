import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "world-of-seeds",
        "version": "1.1.0-dev",
    }


@pytest.mark.asyncio
async def test_readiness_checks_the_database(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "world-of-seeds",
        "version": "1.1.0-dev",
    }


@pytest.mark.asyncio
async def test_api_docs_are_available_during_development(client: AsyncClient) -> None:
    response = await client.get("/api/openapi.json")

    assert response.status_code == 200
