"""Exercise real admin dependencies with independent authentication and CSRF states."""

import re
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_auth import create_user, csrf_header, login

from app.main import app

MISSING_ID = "00000000-0000-4000-8000-000000000001"
# Empty required bodies deliberately reach 422 only after authorization succeeds.
# Bodyless actions run exclusively against the disposable database/config fixture.
MUTATIONS = [
    ("PATCH", "/api/v1/admin/options", 422),
    ("PATCH", "/api/v1/admin/services/newgreedy/config", 422),
    ("DELETE", "/api/v1/admin/services/newgreedy/stats", 503),
    ("POST", "/api/v1/admin/services/newgreedy/restart", 503),
    ("POST", "/api/v1/admin/services/wos/restart", 503),
    ("POST", "/api/v1/admin/users", 200),
    ("PATCH", "/api/v1/admin/users/{user_id}/status", 422),
    ("DELETE", "/api/v1/admin/users/{user_id}", 404),
    ("POST", "/api/v1/admin/external-api-clients", 422),
    ("DELETE", "/api/v1/admin/external-api-clients/{client_id}", 204),
    ("POST", "/api/v2/admin/cleanup/purge", 422),
    ("POST", "/api/v2/admin/runtime/qbittorrent/{torrent_id}/action", 422),
    ("PATCH", "/api/v2/admin/options", 422),
    ("POST", "/api/v2/admin/reconciliation/{managed_torrent_id}/recover", 422),
]


def test_matrix_covers_every_registered_admin_mutation() -> None:
    registered = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if "/admin/" in path
        for method in operations
        if method in {"post", "patch", "put", "delete"}
    }
    assert registered == {(method, path) for method, path, _ in MUTATIONS}


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "template", "admin_status"), MUTATIONS)
@pytest.mark.parametrize(
    "identity",
    ["anonymous", "user", "user_no_csrf", "admin_no_csrf", "admin", "suspended", "deleted"],
)
async def test_admin_mutation_authorization_matrix(
    client: AsyncClient,
    db_session: AsyncSession,
    method: str,
    template: str,
    admin_status: int,
    identity: str,
) -> None:
    headers: dict[str, str] = {}
    if identity != "anonymous":
        user = await create_user(
            db_session,
            username="matrix-user",
            password="correct-horse-battery",
            is_admin=identity not in {"user", "user_no_csrf"},
        )
        await login(client, user.username, "correct-horse-battery")
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        if not identity.endswith("no_csrf"):
            headers = csrf_header(client)
        if identity == "suspended":
            user.is_active = False
        elif identity == "deleted":
            # Do not also suspend: the deletion check must independently reject access.
            user.deleted_at = datetime.now(UTC)
            assert user.is_active
        await db_session.commit()

    path = re.sub(r"\{[^}]+\}", MISSING_ID, template)
    response = await client.request(method, path, json={}, headers=headers)
    expected = (
        admin_status
        if identity == "admin"
        else 401
        if identity in {"anonymous", "suspended", "deleted"}
        else 403
    )
    assert response.status_code == expected, response.text
    if identity == "user":
        # Valid user CSRF isolates the role check from CSRF rejection.
        assert response.json()["detail"]["code"] == "forbidden"
    elif identity.endswith("no_csrf"):
        assert response.json()["detail"]["code"] == "csrf_invalid"
