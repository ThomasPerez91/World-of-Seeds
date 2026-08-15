import json
import uuid
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import hash_password
from app.core.config import Settings
from app.files import WorkspaceManager
from app.integrations import ExternalServicesMonitor
from app.main import app
from app.models import TrashEntry, User


async def create_user(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    is_admin: bool = False,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        is_admin=is_admin,
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def create_workspace_user(
    db: AsyncSession,
    data_root: Path,
    *,
    username: str,
    password: str,
) -> User:
    WorkspaceManager(data_root).create(username)
    return await create_user(db, username=username, password=password)


async def login(client: AsyncClient, username: str, password: str) -> None:
    client.cookies.clear()
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def csrf_header(client: AsyncClient) -> dict[str, str]:
    token = client.cookies.get("wos_csrf")
    assert token is not None
    return {"X-CSRF-Token": token}


@pytest.mark.asyncio
async def test_services_health_is_detailed_and_admin_only(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await create_user(
        db_session,
        username="admin",
        password="admin-password-long",
        is_admin=True,
    )
    await create_user(
        db_session,
        username="regular",
        password="regular-password-long",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"total": 2})
        if request.url.path == "/api/stats":
            return httpx.Response(
                200,
                json={
                    "deadbeef": {
                        "cumul_rep_dl": 1000,
                        "cumul_rep_ul": 1500,
                        "cumul_real_ul": 100,
                        "ann_count": 3,
                        "mode": "down",
                    }
                },
            )
        if request.url.path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.", headers={"Set-Cookie": "SID=test; Path=/"})
        if request.url.path == "/api/v2/app/version":
            return httpx.Response(200, text="v5.1.2")
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(
                200,
                json=[
                    {
                        "hash": "a" * 40,
                        "name": "Film.mkv",
                        "state": "uploading",
                        "progress": 1,
                        "total_size": 1000,
                        "downloaded": 1000,
                        "uploaded": 500,
                        "dlspeed": 0,
                        "upspeed": 100,
                        "ratio": 0.5,
                        "eta": 0,
                        "category": "films",
                        "tracker": "https://tracker.example/announce",
                    }
                ],
            )
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

    await login(client, "regular", "regular-password-long")
    forbidden = await client.get("/api/v1/admin/services/health")
    assert forbidden.status_code == 403
    forbidden_torrents = await client.get("/api/v1/admin/services/qbittorrent/torrents")
    assert forbidden_torrents.status_code == 403

    await login(client, "admin", "admin-password-long")
    response = await client.get("/api/v1/admin/services/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checked_at": response.json()["checked_at"],
        "newgreedy": {
            "status": "healthy",
            "latency_ms": response.json()["newgreedy"]["latency_ms"],
            "version": None,
            "error_code": None,
        },
        "qbittorrent": {
            "status": "healthy",
            "latency_ms": response.json()["qbittorrent"]["latency_ms"],
            "version": "v5.1.2",
            "error_code": None,
        },
    }

    qbittorrent = await client.get("/api/v1/admin/services/qbittorrent/torrents")
    newgreedy = await client.get("/api/v1/admin/services/newgreedy/torrents")

    assert qbittorrent.status_code == 200
    assert qbittorrent.json()["torrents"][0]["name"] == "Film.mkv"
    assert qbittorrent.json()["torrents"][0]["tracker_host"] == "tracker.example"
    assert newgreedy.status_code == 200
    assert newgreedy.json()["torrents"][0]["id"] == "deadbeef"


@pytest.mark.asyncio
async def test_admin_controls_newgreedy_config_and_statistics(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_user(
        db_session,
        username="admin",
        password="admin-password-long",
        is_admin=True,
    )
    control = data_root / ".wos-control" / "newgreedy"
    control.mkdir(parents=True)
    control.chmod(0o700)
    config = control / "config.ini"
    config.write_text(
        """[proxy]
listen_port = 3456
tracker_timeout = 5
[spoofing]
upload_mode = ratio_based
target_ratio = 1.6
auto_stop_at_target = true
[web]
web_enabled = true
web_host = 0.0.0.0
web_port = 8080
""",
        encoding="utf-8",
    )
    config.chmod(0o600)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/stats":
            return httpx.Response(
                200,
                json={
                    "deadbeef": {
                        "cumul_rep_dl": 2_000,
                        "cumul_rep_ul": 3_500,
                        "cumul_real_ul": 500,
                        "mode": "seed",
                    }
                },
            )
        if request.method == "DELETE" and request.url.path == "/api/stats/purge":
            return httpx.Response(200, json={"purged": 1, "remaining": 0})
        raise AssertionError(f"Unexpected integration request: {request.method} {request.url}")

    app.state.external_services_monitor = ExternalServicesMonitor(
        Settings.model_validate({"newgreedy_url": "http://newgreedy:8080"}),
        transport=httpx.MockTransport(handler),
    )
    await login(client, "admin", "admin-password-long")

    loaded = await client.get("/api/v1/admin/services/newgreedy/config")
    assert loaded.status_code == 200
    fields = {
        field["id"]: field for section in loaded.json()["sections"] for field in section["fields"]
    }
    assert fields["proxy.listen_port"]["editable"] is False
    assert fields["spoofing.target_ratio"]["value"] == 1.6

    missing_csrf = await client.patch(
        "/api/v1/admin/services/newgreedy/config",
        json={"changes": {"spoofing.target_ratio": 2.1}},
    )
    assert missing_csrf.status_code == 403

    updated = await client.patch(
        "/api/v1/admin/services/newgreedy/config",
        json={"changes": {"spoofing.target_ratio": 2.1}},
        headers=csrf_header(client),
    )
    assert updated.status_code == 200
    assert updated.json()["restart_required"] is True
    assert "target_ratio = 2.1" in config.read_text(encoding="utf-8")

    overview = await client.get("/api/v1/admin/services/newgreedy/overview")
    assert overview.status_code == 200
    assert overview.json()["torrents"] == 1
    assert overview.json()["total_fake_uploaded_bytes"] == 3_000

    reset = await client.delete(
        "/api/v1/admin/services/newgreedy/stats",
        headers=csrf_header(client),
    )
    assert reset.status_code == 200
    assert reset.json() == {"purged": 1, "remaining": 0}


@pytest.mark.asyncio
async def test_admin_requests_newgreedy_restart_through_control_channel(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    admin = await create_user(
        db_session,
        username="admin",
        password="admin-password-long",
        is_admin=True,
    )
    control_root = data_root / ".wos-control"
    request_directory = control_root / "newgreedy"
    status_directory = control_root / "newgreedy-status"
    request_directory.mkdir(parents=True)
    status_directory.mkdir()
    control_root.chmod(0o700)
    request_directory.chmod(0o700)
    status_directory.chmod(0o750)
    await login(client, "admin", "admin-password-long")

    initial = await client.get("/api/v1/admin/services/newgreedy/restart")
    missing_csrf = await client.post("/api/v1/admin/services/newgreedy/restart")
    requested = await client.post(
        "/api/v1/admin/services/newgreedy/restart",
        headers=csrf_header(client),
    )
    duplicate = await client.post(
        "/api/v1/admin/services/newgreedy/restart",
        headers=csrf_header(client),
    )

    assert initial.status_code == 200
    assert initial.json()["state"] == "idle"
    assert missing_csrf.status_code == 403
    assert requested.status_code == 202
    assert requested.json()["state"] == "pending"
    assert duplicate.status_code == 409
    payload = json.loads((request_directory / "restart-request.json").read_text(encoding="utf-8"))
    assert UUID(payload["requested_by"]) == admin.id


@pytest.mark.asyncio
async def test_storage_overview_is_global_and_admin_only(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_user(
        db_session,
        username="admin",
        password="admin-password-long",
        is_admin=True,
    )
    await create_user(
        db_session,
        username="active-user",
        password="active-password-long",
    )
    await create_user(
        db_session,
        username="suspended-user",
        password="suspended-password-long",
        is_active=False,
    )

    await login(client, "active-user", "active-password-long")
    forbidden = await client.get("/api/v1/admin/storage")
    assert forbidden.status_code == 403

    await login(client, "admin", "admin-password-long")
    response = await client.get("/api/v1/admin/storage")

    assert response.status_code == 200
    overview = response.json()
    assert overview["total"] > 0
    assert overview["used"] >= 0
    assert overview["available"] > 0
    assert overview["active_users"] == 2
    assert overview["suspended_users"] == 1
    assert overview["trash_entries"] == 0
    assert overview["known_trash_bytes"] == 0


@pytest.mark.asyncio
async def test_admin_lists_and_purges_trash_across_users(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_user(
        db_session,
        username="admin",
        password="admin-password-long",
        is_admin=True,
    )
    first = await create_workspace_user(
        db_session,
        data_root,
        username="first-user",
        password="first-password-long",
    )
    second = await create_workspace_user(
        db_session,
        data_root,
        username="second-user",
        password="second-password-long",
    )
    (data_root / first.username / "downloads" / "first.mkv").write_bytes(b"first")
    (data_root / second.username / "downloads" / "second.mkv").write_bytes(b"second")

    await login(client, first.username, "first-password-long")
    first_response = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/first.mkv"},
        headers=csrf_header(client),
    )
    assert first_response.status_code == 201
    first_entry_id = uuid.UUID(first_response.json()["id"])

    await login(client, second.username, "second-password-long")
    second_response = await client.post(
        "/api/v1/trash",
        json={"path": "downloads/second.mkv"},
        headers=csrf_header(client),
    )
    assert second_response.status_code == 201
    second_entry_id = uuid.UUID(second_response.json()["id"])

    forbidden = await client.get("/api/v1/admin/trash")
    assert forbidden.status_code == 403

    await login(client, "admin", "admin-password-long")
    listing = await client.get("/api/v1/admin/trash")
    assert listing.status_code == 200
    assert listing.json()["truncated"] is False
    assert {item["username"] for item in listing.json()["entries"]} == {
        "first-user",
        "second-user",
    }

    missing_csrf = await client.delete(f"/api/v1/admin/trash/{first_entry_id}")
    assert missing_csrf.status_code == 403
    purged_one = await client.delete(
        f"/api/v1/admin/trash/{first_entry_id}",
        headers=csrf_header(client),
    )
    assert purged_one.status_code == 204
    assert await db_session.get(TrashEntry, first_entry_id) is None
    assert await db_session.get(TrashEntry, second_entry_id) is not None

    purged_all = await client.delete(
        "/api/v1/admin/trash",
        headers=csrf_header(client),
    )
    assert purged_all.status_code == 200
    assert purged_all.json() == {"purged": 1, "remaining": 0}
    remaining = await db_session.scalar(select(func.count()).select_from(TrashEntry))
    assert remaining == 0
    assert not (data_root / ".trash" / str(first.id) / str(first_entry_id)).exists()
    assert not (data_root / ".trash" / str(second.id) / str(second_entry_id)).exists()
