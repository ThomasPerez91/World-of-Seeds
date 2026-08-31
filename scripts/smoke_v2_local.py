#!/usr/bin/env python3
"""Run the local V2 API/worker/scheduler/qBittorrent smoke scenario."""

from __future__ import annotations

import hashlib
import http.cookiejar
import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.v2.local"
if not ENV_FILE.exists():
    ENV_FILE = ROOT / ".env.v2.local.example"
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    "world-of-seeds-v2-local",
    "--env-file",
    str(ENV_FILE),
    "-f",
    str(ROOT / "compose.v2.yaml"),
    "-f",
    str(ROOT / "compose.v2.local.yaml"),
]


def compose(*arguments: str, capture: bool = False) -> str:
    result = subprocess.run(
        [*COMPOSE, *arguments],
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def sql(statement: str) -> str:
    return compose(
        "exec",
        "-T",
        "postgres",
        "/bin/sh",
        "-ec",
        'exec psql -XAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"',
        "sh",
        statement,
        capture=True,
    )


def fixture() -> tuple[bytes, str, str]:
    unique = uuid.uuid4().hex.encode()
    name = b"wos-local-smoke-" + unique + b".txt"
    info = (
        b"d6:lengthi1e4:name"
        + str(len(name)).encode()
        + b":"
        + name
        + b"12:piece lengthi16384e6:pieces20:"
        + hashlib.sha1(unique).digest()
        + b"e"
    )
    metainfo = b"d8:announce30:https://c411.org/announce/test4:info" + info + b"e"
    return metainfo, hashlib.sha1(info).hexdigest(), name.decode()


def request_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with opener.open(request, timeout=10) as response:
            value = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected JSON response from {url}")
    return value


def request_bytes(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any, bytes]:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc


def request_empty(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    method: str,
    headers: dict[str, str],
) -> int:
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with opener.open(request, timeout=10) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc


def wait_for(description: str, probe: Any, *, timeout: float = 60) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(1)
    raise RuntimeError(f"timed out waiting for {description}; last value: {last!r}")


def main() -> int:
    published = compose("port", "api", "8000", capture=True)
    port = published.rsplit(":", 1)[-1]
    base = f"http://127.0.0.1:{port}"
    credentials = json.loads(
        compose("exec", "-T", "api", "python", "-m", "app.local_smoke_seed", capture=True)
    )
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    request_json(
        opener,
        f"{base}/api/v1/auth/login",
        method="POST",
        body=json.dumps(credentials).encode(),
        headers={"Content-Type": "application/json"},
    )
    csrf = next((cookie.value for cookie in jar if cookie.name == "wos_csrf"), None)
    if csrf is None:
        raise RuntimeError("login did not return a CSRF cookie")

    content, info_hash, file_name = fixture()
    boundary = f"wos-{uuid.uuid4().hex}"
    multipart = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="torrent"; filename="local-smoke.torrent"\r\n'
            "Content-Type: application/x-bittorrent\r\n\r\n"
        ).encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )

    compose("stop", "worker")
    created = request_json(
        opener,
        f"{base}/api/v2/torrents",
        method="POST",
        body=multipart,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-CSRF-Token": csrf,
        },
    )
    request_id = str(created["id"])
    durable = sql(
        "SELECT state FROM torrent_jobs WHERE torrent_request_id = "
        f"'{request_id}'::uuid AND job_type = 'ADD_TORRENT';"
    )
    if durable != "QUEUED":
        raise RuntimeError(f"job was not durably queued while worker was stopped: {durable}")

    compose("start", "worker")
    wait_for(
        "completed add job",
        lambda: (
            sql(
                "SELECT state = 'COMPLETED' FROM torrent_jobs WHERE torrent_request_id = "
                f"'{request_id}'::uuid AND job_type = 'ADD_TORRENT';"
            )
            == "t"
        ),
        timeout=90,
    )
    listing = wait_for(
        "active request in authenticated API",
        lambda: (
            request_json(opener, f"{base}/api/v2/torrents")
            if any(
                item.get("id") == request_id and item.get("state") in {"active", "ready"}
                for item in request_json(opener, f"{base}/api/v2/torrents").get("items", [])
            )
            else None
        ),
    )

    def qb_count() -> str:
        return compose(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            "import urllib.request; print(urllib.request.urlopen("
            f"'http://qbittorrent:8080/api/v2/torrents/info?hashes={info_hash}', "
            "timeout=5).read().decode())",
            capture=True,
        )

    records = json.loads(wait_for("torrent in qBittorrent", lambda: qb_count() or None))
    if len(records) != 1 or records[0].get("hash", "").lower() != info_hash:
        raise RuntimeError("qBittorrent does not contain exactly the submitted infohash")
    wait_for(
        "scheduler generation",
        lambda: (
            sql(
                "SELECT desired_generation > 0 AND applied_generation = desired_generation "
                "FROM scheduler_state WHERE id = 1;"
            )
            == "t"
        ),
        timeout=90,
    )

    compose("restart", "worker")
    time.sleep(5)
    records_after_restart = json.loads(qb_count())
    job_count = sql(
        "SELECT count(*) FROM torrent_jobs WHERE torrent_request_id = "
        f"'{request_id}'::uuid AND job_type = 'ADD_TORRENT';"
    )
    if len(records_after_restart) != 1 or job_count != "1":
        raise RuntimeError("worker restart duplicated a qB torrent or durable add job")

    compose("stop", "worker")
    storage_key = sql(f"SELECT storage_key FROM managed_torrents WHERE info_hash = '{info_hash}';")
    if len(storage_key) != 36:
        raise RuntimeError("managed storage key is unavailable")
    compose(
        "exec",
        "-T",
        "api",
        "python",
        "-c",
        (
            "from pathlib import Path; import sys; "
            "path=Path('/data/content')/sys.argv[1].replace('-', '')/sys.argv[2]; "
            "path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'x')"
        ),
        storage_key,
        file_name,
    )
    sql(
        "UPDATE managed_torrents SET state = 'READY', progress = 1, ready_at = now(), "
        "retention_expires_at = now() + interval '5 days', updated_at = now() "
        f"WHERE info_hash = '{info_hash}'; "
        "UPDATE torrent_requests SET state = 'READY', ready_at = now(), updated_at = now() "
        f"WHERE id = '{request_id}'::uuid;"
    )
    snapshot = request_json(
        opener,
        f"{base}/api/v2/torrents/{request_id}/download-manifest",
    )
    items = snapshot.get("items")
    if not isinstance(items, list) or len(items) != 1:
        raise RuntimeError("ready download manifest is incomplete")
    file_id = str(items[0]["id"])
    snapshot_id = str(snapshot["snapshot_id"])
    download_url = (
        f"{base}/api/v2/torrents/{request_id}/files/{file_id}/download?snapshot={snapshot_id}"
    )
    range_status, range_headers, range_body = request_bytes(
        opener,
        download_url,
        headers={"Range": "bytes=0-0"},
    )
    if (
        range_status != 206
        or range_body != b"x"
        or range_headers.get("Content-Range") != "bytes 0-0/1"
    ):
        raise RuntimeError("ready Range download is invalid")
    archive_status, _, archive_body = request_bytes(
        opener,
        f"{base}/api/v2/torrents/{request_id}/download-archive?snapshot={snapshot_id}",
    )
    with zipfile.ZipFile(io.BytesIO(archive_body)) as archive:
        if archive_status != 200 or archive.read(file_name) != b"x":
            raise RuntimeError("ready ZIP fallback is invalid")

    cancellation_status = request_empty(
        opener,
        f"{base}/api/v2/torrents/{request_id}",
        method="DELETE",
        headers={"X-CSRF-Token": csrf},
    )
    retained = sql(
        "SELECT mt.state || '|' || tr.state || '|' || tj.state "
        "FROM torrent_requests tr "
        "JOIN managed_torrents mt ON mt.id = tr.managed_torrent_id "
        "JOIN torrent_jobs tj ON tj.torrent_request_id = tr.id "
        "AND tj.job_type = 'PURGE_TORRENT' "
        f"WHERE tr.id = '{request_id}'::uuid;"
    )
    if cancellation_status != 204 or retained != "PURGE_PENDING|CANCELLED|QUEUED":
        raise RuntimeError(f"retained cancellation is invalid: {retained}")
    compose("start", "worker")

    metrics_status, _, metrics_body = request_bytes(opener, f"{base}/api/v2/metrics")
    metrics = metrics_body.decode("utf-8")
    required_metrics = (
        "wos_api_requests_total",
        "wos_jobs",
        "wos_scheduler_generation",
        "wos_download_leases_active",
        "wos_redis_up",
        "wos_qbittorrent_up",
        "wos_storage_bytes",
    )
    if metrics_status != 200 or any(name not in metrics for name in required_metrics):
        raise RuntimeError("application metrics are incomplete")
    if info_hash in metrics or file_name in metrics:
        raise RuntimeError("application metrics contain a business identifier")

    with opener.open(f"{base}/", timeout=10) as response:
        index = response.read().decode("utf-8")
    asset_start = index.find('src="/assets/')
    if asset_start < 0:
        raise RuntimeError("frontend index does not reference its production bundle")
    asset = index[asset_start + 5 : index.find('"', asset_start + 5)]
    with opener.open(f"{base}{asset}", timeout=10) as response:
        bundle = response.read().decode("utf-8")
    if "Mes téléchargements" not in bundle or listing.get("total", 0) < 1:
        raise RuntimeError("frontend bundle and authenticated torrent state are inconsistent")

    print(
        json.dumps(
            {
                "request_id": request_id,
                "info_hash": info_hash,
                "job": "COMPLETED",
                "request_state": next(
                    item["state"] for item in listing["items"] if item["id"] == request_id
                ),
                "qbittorrent_matches": 1,
                "worker_restart_duplicates": 0,
                "scheduler_applied": True,
                "range_download_checked": True,
                "zip_fallback_checked": True,
                "retained_cancellation_checked": True,
                "secret_safe_metrics_checked": True,
                "ui_bundle_checked": True,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"V2 local smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
