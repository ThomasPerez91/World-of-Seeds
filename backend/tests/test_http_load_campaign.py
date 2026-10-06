import hashlib
import json
import uuid
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.benchmark_http_downloads import ClientResult, Manifest, Target, download_one, read_manifest


def target(**kwargs: object) -> Target:
    return Target.model_validate(
        {
            "path": f"/api/v2/torrents/{uuid.uuid4()}/files/{uuid.uuid4()}/download",
            "session_token": SecretStr("private-session-token-" + "x" * 32),
            "expected_bytes": 8,
            **kwargs,
        }
    )


@pytest.mark.asyncio
async def test_campaign_cancellation_resume_verifies_content_and_leaks_no_credentials() -> None:
    payload = b"12345678"
    calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200, headers={"etag": '"stable"'}, stream=httpx.ByteStream(payload[:4])
            )
        assert request.headers["Range"] == "bytes=4-"
        assert request.headers["If-Range"] == '"stable"'
        return httpx.Response(
            206,
            headers={"etag": '"stable"', "Content-Range": "bytes 4-7/8"},
            stream=httpx.ByteStream(payload[4:]),
        )

    spec = target(
        cancel_after_bytes=4,
        resume_after_cancel=True,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    manifest = Manifest(base_url="https://example.test", targets=[spec])
    result = ClientResult(0)
    async with httpx.AsyncClient(
        base_url=manifest.base_url, transport=httpx.MockTransport(handle)
    ) as client:
        await download_one(client, manifest, spec, result)
    assert result.outcome == "completed"
    assert result.bytes_received == 8
    assert result.resumed and result.checksum_verified
    assert "private-session" not in repr(result)


@pytest.mark.asyncio
async def test_campaign_rejects_resume_with_changed_etag() -> None:
    calls = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200, headers={"etag": '"stable"'}, stream=httpx.ByteStream(b"1234")
            )
        return httpx.Response(
            206,
            headers={"etag": '"changed"', "Content-Range": "bytes 4-7/8"},
            stream=httpx.ByteStream(b"5678"),
        )

    spec = target(cancel_after_bytes=4, resume_after_cancel=True)
    manifest = Manifest(base_url="https://example.test", targets=[spec])
    result = ClientResult(0)
    async with httpx.AsyncClient(
        base_url=manifest.base_url, transport=httpx.MockTransport(handle)
    ) as client:
        await download_one(client, manifest, spec, result)
    assert result.outcome == "error"
    assert result.error_type == "ValueError"


@pytest.mark.parametrize(
    "origin",
    ["http://example.test", "https://user:password@example.test", "https://example.test/other"],
)
def test_campaign_refuses_unprotected_or_credential_bearing_origins(origin: str) -> None:
    with pytest.raises(ValueError):
        Manifest(base_url=origin, targets=[target()]).check()


def test_campaign_manifest_requires_private_permissions_and_download_paths(tmp_path: Path) -> None:
    spec = target()
    document = {
        "base_url": "https://example.test",
        "targets": [
            {
                "path": spec.path,
                "session_token": spec.session_token.get_secret_value(),
                "expected_bytes": 8,
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document))
    path.chmod(0o644)
    with pytest.raises(ValueError):
        read_manifest(path)
    path.chmod(0o600)
    assert len(read_manifest(path).targets) == 1
    invalid_target = {
        "path": "/api/v1/admin/users",
        "session_token": spec.session_token.get_secret_value(),
        "expected_bytes": 8,
    }
    path.write_text(json.dumps({"base_url": "https://example.test", "targets": [invalid_target]}))
    with pytest.raises(ValueError):
        read_manifest(path)


@pytest.mark.asyncio
async def test_slow_reader_cannot_bank_idle_retry_or_reconnect_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = 0.0
    delays: list[float] = []
    calls = 0

    async def sleep(delay: float) -> None:
        nonlocal clock
        delays.append(delay)
        clock += delay

    async def handle(request: httpx.Request) -> httpx.Response:
        nonlocal clock, calls
        calls += 1
        clock += 100  # Admission and reconnect delays dwarf the file's read time.
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        if calls == 2:
            return httpx.Response(
                200, headers={"etag": '"stable"'}, stream=httpx.ByteStream(b"1234")
            )
        assert request.headers["Range"] == "bytes=4-"
        return httpx.Response(
            206,
            headers={"etag": '"stable"', "Content-Range": "bytes 4-7/8"},
            stream=httpx.ByteStream(b"5678"),
        )

    monkeypatch.setattr("app.benchmark_http_downloads.perf_counter", lambda: clock)
    monkeypatch.setattr("app.benchmark_http_downloads.asyncio.sleep", sleep)
    spec = target(read_bytes_per_second=4, cancel_after_bytes=4, resume_after_cancel=True)
    manifest = Manifest(base_url="https://example.test", targets=[spec])
    result = ClientResult(0)
    async with httpx.AsyncClient(
        base_url=manifest.base_url, transport=httpx.MockTransport(handle)
    ) as client:
        await download_one(client, manifest, spec, result)
    assert result.outcome == "completed" and result.resumed
    assert result.retries_429 == 1
    assert delays == [2, 1, 1]
