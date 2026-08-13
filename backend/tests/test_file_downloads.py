import os
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope

from app.auth.security import hash_password
from app.files import WorkspaceManager
from app.files.downloads import (
    DOWNLOAD_CHUNK_SIZE,
    DownloadStreamingResponse,
    SandboxedFileDownloader,
    stream_download,
)
from app.models import User


async def create_workspace_user(
    db: AsyncSession,
    data_root: Path,
    *,
    username: str = "thomas",
    password: str = "correct-horse-battery",
    must_change_credentials: bool = False,
) -> None:
    WorkspaceManager(data_root).create(username)
    db.add(
        User(
            username=username,
            password_hash=hash_password(password),
            must_change_credentials=must_change_credentials,
        )
    )
    await db.commit()


async def login(client: AsyncClient, username: str = "thomas") -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text


def user_downloads(data_root: Path, username: str = "thomas") -> Path:
    return data_root / username / "downloads"


@pytest.mark.asyncio
async def test_full_download_and_head_expose_resume_headers(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    content = b"0123456789"
    file_name = 'episode special "ete".mkv'
    (user_downloads(data_root) / file_name).write_bytes(content)
    await login(client)

    response = await client.get(
        "/api/v1/files/download",
        params={"path": f"downloads/{file_name}"},
    )
    head = await client.head(
        "/api/v1/files/download",
        params={"path": f"downloads/{file_name}"},
    )

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["content-type"] in {"video/x-matroska", "video/matroska"}
    assert response.headers["etag"].startswith('"')
    assert response.headers["last-modified"].endswith("GMT")
    assert "attachment" in response.headers["content-disposition"]
    assert (
        "filename*=UTF-8''episode%20special%20%22ete%22.mkv"
        in response.headers["content-disposition"]
    )
    assert head.status_code == 200
    assert head.content == b""
    assert head.headers["content-length"] == str(len(content))
    assert head.headers["etag"] == response.headers["etag"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("range_header", "expected_content", "expected_content_range"),
    [
        ("bytes=0-3", b"0123", "bytes 0-3/10"),
        ("bytes=4-", b"456789", "bytes 4-9/10"),
        ("bytes=-3", b"789", "bytes 7-9/10"),
        ("bytes=7-50", b"789", "bytes 7-9/10"),
        ("bytes=-50", b"0123456789", "bytes 0-9/10"),
    ],
)
async def test_single_byte_ranges_return_partial_content(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    range_header: str,
    expected_content: bytes,
    expected_content_range: str,
) -> None:
    await create_workspace_user(db_session, data_root)
    (user_downloads(data_root) / "movie.mkv").write_bytes(b"0123456789")
    await login(client)

    response = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/movie.mkv"},
        headers={"Range": range_header},
    )

    assert response.status_code == 206
    assert response.content == expected_content
    assert response.headers["content-length"] == str(len(expected_content))
    assert response.headers["content-range"] == expected_content_range


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "range_header",
    [
        "bytes=10-",
        "bytes=8-2",
        "bytes=0-1,4-5",
        "bytes=-0",
        "bytes=-",
        "items=0-1",
        f"bytes={'9' * 21}-",
    ],
)
async def test_invalid_or_unsatisfiable_ranges_return_416(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    range_header: str,
) -> None:
    await create_workspace_user(db_session, data_root)
    (user_downloads(data_root) / "movie.mkv").write_bytes(b"0123456789")
    await login(client)

    response = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/movie.mkv"},
        headers={"Range": range_header},
    )

    assert response.status_code == 416
    assert response.content == b""
    assert response.headers["content-range"] == "bytes */10"
    assert response.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_if_range_prevents_resuming_a_changed_file(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    (user_downloads(data_root) / "movie.mkv").write_bytes(b"0123456789")
    await login(client)
    metadata = await client.head(
        "/api/v1/files/download",
        params={"path": "downloads/movie.mkv"},
    )

    matching_etag = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/movie.mkv"},
        headers={"Range": "bytes=5-", "If-Range": metadata.headers["etag"]},
    )
    matching_date = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/movie.mkv"},
        headers={"Range": "bytes=5-", "If-Range": metadata.headers["last-modified"]},
    )
    stale = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/movie.mkv"},
        headers={"Range": "bytes=5-", "If-Range": '"stale-etag"'},
    )

    assert matching_etag.status_code == 206
    assert matching_etag.content == b"56789"
    assert matching_date.status_code == 206
    assert matching_date.content == b"56789"
    assert stale.status_code == 200
    assert stale.content == b"0123456789"
    assert "content-range" not in stale.headers


@pytest.mark.asyncio
async def test_empty_file_downloads_but_has_no_satisfiable_range(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    (user_downloads(data_root) / "empty.mkv").touch()
    await login(client)

    full = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/empty.mkv"},
    )
    partial = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/empty.mkv"},
        headers={"Range": "bytes=0-"},
    )

    assert full.status_code == 200
    assert full.content == b""
    assert full.headers["content-length"] == "0"
    assert partial.status_code == 416
    assert partial.headers["content-range"] == "bytes */0"


@pytest.mark.asyncio
async def test_download_is_chunked_and_closes_its_descriptor(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_workspace_user(db_session, data_root)
    content = b"x" * (DOWNLOAD_CHUNK_SIZE * 2 + 37)
    (user_downloads(data_root) / "large.bin").write_bytes(content)
    await login(client)

    original_pread = os.pread
    calls: list[tuple[int, int, int]] = []

    def recording_pread(file_descriptor: int, size: int, offset: int) -> bytes:
        calls.append((file_descriptor, size, offset))
        return original_pread(file_descriptor, size, offset)

    monkeypatch.setattr(os, "pread", recording_pread)
    response = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/large.bin"},
    )

    assert response.status_code == 200
    assert response.content == content
    assert len(calls) == 3
    assert max(size for _, size, _ in calls) <= DOWNLOAD_CHUNK_SIZE
    with pytest.raises(OSError):
        os.fstat(calls[0][0])


@pytest.mark.asyncio
async def test_interrupted_stream_closes_its_descriptor(data_root: Path) -> None:
    WorkspaceManager(data_root).create("thomas")
    content = b"x" * (DOWNLOAD_CHUNK_SIZE + 1)
    (user_downloads(data_root) / "interrupted.bin").write_bytes(content)
    download = SandboxedFileDownloader(WorkspaceManager(data_root)).open(
        "thomas",
        "downloads/interrupted.bin",
    )
    file_descriptor = download.file_descriptor
    stream = stream_download(download, start=0, length=download.size)

    first_chunk = await anext(stream)
    await stream.aclose()

    assert len(first_chunk) == DOWNLOAD_CHUNK_SIZE
    with pytest.raises(OSError):
        os.fstat(file_descriptor)


@pytest.mark.asyncio
async def test_disconnect_before_response_start_closes_its_descriptor(data_root: Path) -> None:
    WorkspaceManager(data_root).create("thomas")
    (user_downloads(data_root) / "interrupted.bin").write_bytes(b"content")
    download = SandboxedFileDownloader(WorkspaceManager(data_root)).open(
        "thomas",
        "downloads/interrupted.bin",
    )
    file_descriptor = download.file_descriptor
    response = DownloadStreamingResponse(
        download,
        start=0,
        length=download.size,
        status_code=200,
        headers={"Content-Length": str(download.size)},
    )
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/files/download",
        "raw_path": b"/api/v1/files/download",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def disconnect(_: Message) -> None:
        raise OSError("client disconnected before response start")

    with pytest.raises(ClientDisconnect):
        await response(scope, receive, disconnect)

    download.close()
    with pytest.raises(OSError):
        os.fstat(file_descriptor)


@pytest.mark.asyncio
async def test_sparse_multi_gigabyte_file_can_be_resumed_without_full_read(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    huge_file = user_downloads(data_root) / "huge.mkv"
    with huge_file.open("wb") as file_handle:
        file_handle.truncate(40 * 1024**3)
    await login(client)

    response = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/huge.mkv"},
        headers={"Range": "bytes=-8"},
    )

    assert response.status_code == 206
    assert response.content == b"\0" * 8
    file_size = 40 * 1024**3
    assert response.headers["content-range"] == f"bytes {file_size - 8}-{file_size - 1}/{file_size}"


@pytest.mark.asyncio
async def test_download_rejects_traversal_symlinks_directories_and_other_users(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    tmp_path: Path,
) -> None:
    await create_workspace_user(db_session, data_root)
    WorkspaceManager(data_root).create("other")
    (user_downloads(data_root, "other") / "secret.txt").write_text(
        "secret",
        encoding="utf-8",
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside-secret", encoding="utf-8")
    (user_downloads(data_root) / "escape").symlink_to(outside)
    await login(client)

    traversal = await client.get(
        "/api/v1/files/download",
        params={"path": "../other/downloads/secret.txt"},
    )
    symlink = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/escape"},
    )
    directory = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads"},
    )

    assert traversal.status_code == 400
    assert symlink.status_code == 403
    assert directory.status_code == 400
    assert "secret" not in traversal.text
    assert "outside-secret" not in symlink.text


@pytest.mark.asyncio
async def test_download_reports_metadata_permission_errors_as_blocked(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await create_workspace_user(db_session, data_root)
    (user_downloads(data_root) / "movie.mkv").write_bytes(b"video")
    await login(client)
    original_stat = os.stat

    def deny_movie_metadata(
        path: str,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        if path == "movie.mkv":
            raise PermissionError("metadata denied")
        return original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "stat", deny_movie_metadata)

    response = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/movie.mkv"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Path is blocked"


@pytest.mark.asyncio
async def test_download_requires_authenticated_current_credentials(
    client: AsyncClient,
    db_session: AsyncSession,
    data_root: Path,
) -> None:
    anonymous = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/movie.mkv"},
    )
    await create_workspace_user(
        db_session,
        data_root,
        username="temporary",
        must_change_credentials=True,
    )
    await login(client, "temporary")
    forced_change = await client.get(
        "/api/v1/files/download",
        params={"path": "downloads/movie.mkv"},
    )

    assert anonymous.status_code == 401
    assert forced_change.status_code == 403
