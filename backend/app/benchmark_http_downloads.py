"""Read-only HTTP load campaign; private session manifest, aggregate public report."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, SecretStr, ValidationError

_PATH = re.compile(r"^/api/v2/torrents/[a-f0-9-]{36}/files/[a-f0-9-]{36}/download$")
PROMETHEUS_QUERIES = {
    "cpu_busy_ratio": '1-avg(rate(node_cpu_seconds_total{mode="idle"}[1m]))',
    "cpu_iowait_ratio": 'avg(rate(node_cpu_seconds_total{mode="iowait"}[1m]))',
    "disk_busy_ratio": "max(rate(node_disk_io_time_seconds_total[1m]))",
    "qb_download_bytes_per_second": "max(wos_torrent_qb_global_download_rate_bytes_per_second)",
    "qb_upload_bytes_per_second": "max(wos_torrent_qb_global_upload_rate_bytes_per_second)",
    "http_waiting": "max(wos_http_download_waiting_streams)",
    "http_fast": "max(wos_http_download_fast_streams)",
    "oldest_wait_seconds": "max(wos_http_download_waiting_oldest_age_seconds)",
    "host_upload_bytes_per_second": "max(wos_http_download_host_upload_bytes_per_second)",
    "network_telemetry_fresh": "min(wos_http_download_telemetry_fresh)",
}


class Target(BaseModel):
    path: str
    session_token: SecretStr
    expected_bytes: int = Field(gt=0)
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    read_bytes_per_second: int = Field(default=0, ge=0, le=125_000_000)
    cancel_after_bytes: int = Field(default=0, ge=0)
    resume_after_cancel: bool = False


class Manifest(BaseModel):
    base_url: str
    cookie_name: str = Field(default="wos_session", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    targets: list[Target] = Field(min_length=1, max_length=200)

    def check(self) -> None:
        parsed = urlsplit(self.base_url)
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("invalid campaign origin")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("HTTPS required outside loopback")
        for target in self.targets:
            if not _PATH.fullmatch(target.path):
                raise ValueError("only managed READY file download paths are allowed")
            if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", target.session_token.get_secret_value()):
                raise ValueError("invalid session token")
            if (
                target.resume_after_cancel
                and not 0 < target.cancel_after_bytes < target.expected_bytes
            ):
                raise ValueError("resume scenario needs a partial cancellation")


def read_manifest(path: Path) -> Manifest:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("manifest must be an owned regular file with mode 0600")
        with os.fdopen(os.dup(fd), "rb") as source:
            content = source.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise ValueError("manifest too large")
        manifest = Manifest.model_validate_json(content)
        manifest.check()
        return manifest
    finally:
        os.close(fd)


@dataclass
class ClientResult:
    index: int
    outcome: str = "pending"
    bytes_received: int = 0
    first_byte_seconds: float | None = None
    duration_seconds: float = 0
    retries_429: int = 0
    resumed: bool = False
    checksum_verified: bool = False
    error_type: str | None = None


async def download_one(
    client: httpx.AsyncClient, manifest: Manifest, target: Target, result: ClientResult
) -> None:
    started = perf_counter()
    digest = hashlib.sha256()
    etag: str | None = None
    interrupted = False
    try:
        while result.bytes_received < target.expected_bytes:
            headers = {
                "Cookie": f"{manifest.cookie_name}={target.session_token.get_secret_value()}"
            }
            if result.resumed:
                headers["Range"] = f"bytes={result.bytes_received}-"
                if etag:
                    headers["If-Range"] = etag
            async with client.stream("GET", target.path, headers=headers) as response:
                if response.status_code == 429:
                    result.retries_429 += 1
                    if result.retries_429 > 10:
                        result.outcome = "rejected"
                        return
                    try:
                        delay = min(60, max(1, int(response.headers.get("Retry-After", "2"))))
                    except ValueError:
                        delay = 2
                    await asyncio.sleep(delay)
                    continue
                if response.status_code != (206 if result.resumed else 200):
                    result.outcome = "error"
                    result.error_type = f"http_{response.status_code}"
                    return
                if result.resumed:
                    expected = (
                        f"bytes {result.bytes_received}-{target.expected_bytes - 1}/"
                        f"{target.expected_bytes}"
                    )
                    if (
                        response.headers.get("Content-Range") != expected
                        or response.headers.get("etag") != etag
                    ):
                        raise ValueError("range snapshot changed")
                else:
                    etag = response.headers.get("etag")
                async for chunk in response.aiter_raw():
                    if result.first_byte_seconds is None:
                        result.first_byte_seconds = perf_counter() - started
                    result.bytes_received += len(chunk)
                    digest.update(chunk)
                    if target.read_bytes_per_second:
                        # Idle admission/retry/reconnect time cannot buy burst allowance.
                        await asyncio.sleep(len(chunk) / target.read_bytes_per_second)
                    if (
                        target.cancel_after_bytes
                        and not interrupted
                        and result.bytes_received >= target.cancel_after_bytes
                    ):
                        interrupted = True
                        break
            if interrupted and not result.resumed:
                if not target.resume_after_cancel:
                    result.outcome = "cancelled"
                    return
                result.resumed = True
                continue
            break
        if result.bytes_received != target.expected_bytes:
            raise ValueError("incomplete file")
        if target.expected_sha256:
            if digest.hexdigest() != target.expected_sha256:
                raise ValueError("checksum mismatch")
            result.checksum_verified = True
        result.outcome = "completed"
    except asyncio.CancelledError:
        result.outcome = "time_budget_reached"
    except (httpx.HTTPError, ValueError) as exc:
        result.outcome = "error"
        # Never serialize exception text: it can contain a URL or private identifiers.
        result.error_type = type(exc).__name__
    finally:
        result.duration_seconds = perf_counter() - started


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


async def campaign(
    manifest: Manifest,
    *,
    seconds: int,
    prometheus_url: str | None = None,
    warmup_clients: int = 0,
    warmup_seconds: float = 0,
) -> dict[str, object]:
    manifest.check()
    if not 0 <= warmup_clients < len(manifest.targets) or not 0 <= warmup_seconds <= seconds:
        raise ValueError("invalid warmup")
    results = [ClientResult(index) for index in range(len(manifest.targets))]
    probe_latencies: dict[str, list[float]] = {"live": [], "ready": []}
    probe_errors = {"live": 0, "ready": 0}
    system_samples: list[dict[str, float | None]] = []
    stopped = asyncio.Event()
    started = perf_counter()
    limits = httpx.Limits(
        max_connections=len(results) + 5, max_keepalive_connections=len(results) + 5
    )
    async with httpx.AsyncClient(
        base_url=manifest.base_url, limits=limits, timeout=30, trust_env=False
    ) as client:

        async def probes() -> None:
            while not stopped.is_set():
                for kind in probe_latencies:
                    before = perf_counter()
                    try:
                        response = await client.get(f"/api/v1/health/{kind}")
                        if response.status_code == 200:
                            probe_latencies[kind].append(perf_counter() - before)
                        else:
                            probe_errors[kind] += 1
                    except httpx.HTTPError:
                        probe_errors[kind] += 1
                await asyncio.sleep(0.25)

        async def collect_system() -> None:
            if prometheus_url is None:
                return
            async with httpx.AsyncClient(
                base_url=prometheus_url, timeout=5, trust_env=False
            ) as prom:
                while not stopped.is_set():
                    sample: dict[str, float | None] = {}
                    for name, query in PROMETHEUS_QUERIES.items():
                        try:
                            response = await prom.get("/api/v1/query", params={"query": query})
                            response.raise_for_status()
                            data = response.json()["data"]["result"]
                            value = float(data[0]["value"][1]) if data else None
                            sample[name] = (
                                value if value is not None and math.isfinite(value) else None
                            )
                        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                            sample[name] = None
                    system_samples.append(sample)
                    await asyncio.sleep(5)

        probe_task = asyncio.create_task(probes())
        system_task = asyncio.create_task(collect_system())
        tasks: list[asyncio.Task[None]] = []
        try:
            for index, (target, result) in enumerate(zip(manifest.targets, results, strict=True)):
                if warmup_clients and index == warmup_clients:
                    await asyncio.sleep(warmup_seconds)
                tasks.append(asyncio.create_task(download_one(client, manifest, target, result)))
            _, pending = await asyncio.wait(
                tasks, timeout=max(0, seconds - (perf_counter() - started))
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            stopped.set()
            probe_task.cancel()
            system_task.cancel()
            await asyncio.gather(probe_task, system_task, return_exceptions=True)
    elapsed = perf_counter() - started
    return {
        "clients": len(results),
        "elapsed_seconds": elapsed,
        "client_bytes_received": sum(result.bytes_received for result in results),
        "health": {
            kind: {
                "count": len(values),
                "errors": probe_errors[kind],
                "p95_seconds": percentile(values, 0.95),
                "max_seconds": max(values, default=0),
            }
            for kind, values in probe_latencies.items()
        },
        "clients_results": [asdict(result) for result in results],
        "prometheus_samples": system_samples,
        "production_capacity_validated": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--prometheus-url")
    args = parser.parse_args()
    if not 1 <= args.seconds <= 1800:
        parser.error("duration must be between 1 and 1800 seconds")
    try:
        manifest = read_manifest(args.manifest)
    except (OSError, ValidationError, ValueError):
        parser.error("invalid or insufficiently protected load manifest")
    report = asyncio.run(
        campaign(manifest, seconds=args.seconds, prometheus_url=args.prometheus_url)
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
