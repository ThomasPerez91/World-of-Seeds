import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations import ExternalServicesMonitor
from app.main import app


@pytest.mark.asyncio
async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "world-of-seeds",
        "version": "1.2.0",
    }


@pytest.mark.asyncio
async def test_readiness_checks_the_database(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "world-of-seeds",
        "version": "1.2.0",
    }


@pytest.mark.asyncio
async def test_public_status_includes_external_services_without_disclosing_their_names(
    client: AsyncClient,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"total": 1})
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.", headers={"Set-Cookie": "SID=test; Path=/"})
        if request.url.path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.1.2")
        if request.url.path == "/api/v2/auth/logout":
            return httpx.Response(200)
        raise AssertionError(f"Unexpected integration request: {request.method} {request.url}")

    app.state.external_services_monitor = ExternalServicesMonitor(
        Settings.model_validate(
            {
                "newgreedy_url": "http://newgreedy:8080",
                "qbittorrent_url": "http://qbittorrent:8080",
                "qbittorrent_username": "admin",
                "qbittorrent_password": SecretStr("secret-password"),
            }
        ),
        transport=httpx.MockTransport(handler),
    )

    response = await client.get("/api/v1/health/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert "checked_at" in payload
    assert "newgreedy" not in payload
    assert "qbittorrent" not in payload


@pytest.mark.asyncio
async def test_public_status_is_degraded_when_integrations_are_not_configured(
    client: AsyncClient,
) -> None:
    response = await client.get("/api/v1/health/status")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_api_docs_are_available_during_development(client: AsyncClient) -> None:
    response = await client.get("/api/openapi.json")

    assert response.status_code == 200
