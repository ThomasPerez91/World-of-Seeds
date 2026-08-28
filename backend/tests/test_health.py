from datetime import UTC, datetime

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.coordination import RedisHealth
from app.core.config import Settings
from app.integrations import ExternalServicesMonitor
from app.integrations.types import ExternalServicesSnapshot
from app.main import app


def healthy_integration_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/health":
        return httpx.Response(200, json={"total": 1})
    if request.url.path == "/api/v2/auth/login":
        return httpx.Response(200, text="Ok.", headers={"Set-Cookie": "SID=test; Path=/"})
    if request.url.path == "/api/v2/app/version":
        return httpx.Response(200, text="v5.1.2")
    if request.url.path == "/api/v2/auth/logout":
        return httpx.Response(200)
    raise AssertionError(f"Unexpected integration request: {request.method} {request.url}")


class UnavailableRedisCoordinator:
    async def check_health(self) -> RedisHealth:
        return RedisHealth(
            state="unavailable",
            checked_at=datetime.now(UTC),
            error_code="redis_unavailable",
        )


class TransactionAssertingMonitor:
    def __init__(self, session: AsyncSession, delegate: ExternalServicesMonitor) -> None:
        self._session = session
        self._delegate = delegate

    async def snapshot(self) -> ExternalServicesSnapshot:
        assert self._session.in_transaction() is False
        return await self._delegate.snapshot()


@pytest.mark.asyncio
async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "world-of-seeds",
        "version": __version__,
    }


@pytest.mark.asyncio
async def test_readiness_checks_the_database(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "world-of-seeds",
        "version": __version__,
    }


@pytest.mark.asyncio
async def test_public_status_includes_external_services_without_disclosing_their_names(
    client: AsyncClient,
) -> None:
    app.state.external_services_monitor = ExternalServicesMonitor(
        Settings.model_validate(
            {
                "newgreedy_url": "http://newgreedy:8080",
                "qbittorrent_url": "http://qbittorrent:8080",
                "qbittorrent_username": "admin",
                "qbittorrent_password": SecretStr("secret-password"),
            }
        ),
        transport=httpx.MockTransport(healthy_integration_handler),
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
    db_session: AsyncSession,
) -> None:
    app.state.external_services_monitor = TransactionAssertingMonitor(
        db_session,
        ExternalServicesMonitor(Settings()),
    )
    response = await client.get("/api/v1/health/status")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_configured_redis_failure_degrades_status_but_not_readiness(
    client: AsyncClient,
) -> None:
    app.state.external_services_monitor = ExternalServicesMonitor(
        Settings.model_validate(
            {
                "newgreedy_url": "http://newgreedy:8080",
                "qbittorrent_url": "http://qbittorrent:8080",
                "qbittorrent_username": "admin",
                "qbittorrent_password": SecretStr("secret-password"),
            }
        ),
        transport=httpx.MockTransport(healthy_integration_handler),
    )
    app.state.redis_coordinator = UnavailableRedisCoordinator()

    status_response = await client.get("/api/v1/health/status")
    readiness_response = await client.get("/api/v1/health/ready")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == "degraded"
    assert readiness_response.status_code == 200


@pytest.mark.asyncio
async def test_api_docs_are_available_during_development(client: AsyncClient) -> None:
    response = await client.get("/api/openapi.json")

    assert response.status_code == 200
