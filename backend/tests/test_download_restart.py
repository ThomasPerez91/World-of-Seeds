"""Real SIGTERM, lease cleanup, restart and Range/ETag/SHA-256 over TCP."""

import asyncio
import hashlib
import signal
import socket
import sys
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.local_http_download_load import local_fixture
from app.models import DownloadLease

CHILD = """
import socket, sys
from collections.abc import AsyncIterator
from pathlib import Path
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app import main
from app.server import DrainingServer, REQUEST_GRACE_SECONDS
root = Path(sys.argv[1])
settings = Settings(data_root=root / 'data', runtime_profile='v2', allowed_hosts=['127.0.0.1'])
engine = create_async_engine(f'sqlite+aiosqlite:///{root / "load.sqlite"}')
factory = async_sessionmaker(engine, expire_on_commit=False)
async def db() -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
main.engine = engine
app = main.create_app(settings)
app.dependency_overrides[get_db_session] = db
app.dependency_overrides[get_settings] = lambda: settings
server = DrainingServer(uvicorn.Config(app, log_level='critical', access_log=False,
    timeout_graceful_shutdown=REQUEST_GRACE_SECONDS), app.state.request_drain)
server.run(sockets=[socket.socket(fileno=int(sys.argv[2]))])
"""


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal and inherited listening socket")
async def test_sigterm_cleans_leases_and_restart_resumes_identical_file(tmp_path: Path) -> None:
    async with local_fixture(tmp_path, 1, serve=False) as (manifest, application):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        process: asyncio.subprocess.Process | None = None

        async def start() -> asyncio.subprocess.Process:
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                CHILD,
                str(tmp_path),
                str(sock.fileno()),
                pass_fds=(sock.fileno(),),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(10):
                    async with httpx.AsyncClient(timeout=0.5, trust_env=False) as probe:
                        while True:
                            if child.returncode is not None:
                                raise AssertionError(
                                    (await child.stderr.read()) if child.stderr else b""
                                )
                            try:
                                if (
                                    await probe.get(f"{origin}/api/v1/health/ready")
                                ).status_code == 200:
                                    return child
                            except httpx.HTTPError:
                                pass
                            await asyncio.sleep(0.05)
            except BaseException:
                if child.returncode is None:
                    child.kill()
                await child.wait()
                raise

        async def lease_count() -> int:
            from app.core.database import get_db_session

            dependency = application.dependency_overrides[get_db_session]
            count = -1
            async for session in dependency():
                count = int(await session.scalar(select(func.count()).select_from(DownloadLease)))
            assert count >= 0
            return count

        try:
            process = await start()
            target = manifest.targets[0]
            headers = {"Cookie": f"wos_session={target.session_token.get_secret_value()}"}
            digest = hashlib.sha256()
            received = 0
            async with httpx.AsyncClient(base_url=origin, timeout=15, trust_env=False) as client:
                async with client.stream("GET", target.path, headers=headers) as response:
                    assert response.status_code == 200
                    etag = response.headers["ETag"]
                    chunks = response.aiter_bytes()
                    first = await anext(chunks)
                    received += len(first)
                    digest.update(first)
                    assert await lease_count() == 1
                    before = time.monotonic()
                    process.send_signal(signal.SIGTERM)
                    await asyncio.wait_for(process.wait(), 9)
                    assert time.monotonic() - before < 9
                    assert process.returncode in {0, -signal.SIGTERM}
                    assert await lease_count() == 0
                    with pytest.raises(httpx.RemoteProtocolError):
                        async for chunk in chunks:
                            received += len(chunk)
                            digest.update(chunk)
                assert 0 < received < target.expected_bytes
                process = await start()
                headers.update({"Range": f"bytes={received}-", "If-Range": etag})
                async with client.stream("GET", target.path, headers=headers) as response:
                    assert response.status_code == 206
                    assert response.headers["ETag"] == etag
                    assert response.headers["Content-Range"] == (
                        f"bytes {received}-{target.expected_bytes - 1}/{target.expected_bytes}"
                    )
                    async for chunk in response.aiter_bytes():
                        digest.update(chunk)
                        received += len(chunk)
                assert received == target.expected_bytes
                assert digest.hexdigest() == target.expected_sha256
                # Completion cleanup may finish just after the last client byte.
                async with asyncio.timeout(2):
                    while await lease_count():  # noqa: ASYNC110 -- external SQL state
                        await asyncio.sleep(0.01)
        finally:
            if process is not None and process.returncode is None:
                process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), 9)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            sock.close()
