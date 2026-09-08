from __future__ import annotations

import asyncio
import json
import math
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from app.integrations.account_routing import DeploymentAccountSpec, parse_deployment_account_specs

MAX_QB_TORRENTS = 200
MAX_NEWGREEDY_TORRENTS = 200
DEFAULT_PORT = 9101
DEFAULT_SECRET_PATH = Path("/run/secrets/integration_registry")
DEFAULT_NEWGREEDY_STATS_PATH = Path("/newgreedy/stats.json")
_HASH_RE = re.compile(r"^[0-9a-f]{40}$")
_HASH8_RE = re.compile(r"^[0-9a-f]{8}$")
_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CACHE_SECONDS = 10.0


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _finite_number(value: object, *, minimum: float = 0.0) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        return None
    return number


def _metric(name: str, value: float | int, labels: dict[str, str] | None = None) -> str:
    suffix = ""
    if labels:
        rendered = ",".join(
            f'{key}="{_escape_label(label)}"' for key, label in sorted(labels.items())
        )
        suffix = "{" + rendered + "}"
    return f"{name}{suffix} {value}"


def _load_specs(secret_path: Path) -> tuple[DeploymentAccountSpec, ...]:
    raw = secret_path.read_text(encoding="utf-8")
    return parse_deployment_account_specs(SecretStr(raw))


async def _read_json_response(
    response: httpx.Response,
    *,
    limit: int = 2 * 1024 * 1024,
) -> Any:
    payload = await response.aread()
    if len(payload) > limit:
        raise RuntimeError("integration response too large")
    return json.loads(payload)


async def _fetch_qb_route(
    client: httpx.AsyncClient,
    spec: DeploymentAccountSpec,
) -> tuple[list[dict[str, object]], dict[str, object], bool]:
    base_url = spec.qbittorrent_url.rstrip("/")
    headers = {"Origin": base_url, "Referer": f"{base_url}/"}
    logged_in = False
    login = await client.post(
        f"{base_url}/api/v2/auth/login",
        data={
            "username": spec.qbittorrent_username,
            "password": spec.qbittorrent_password.get_secret_value(),
        },
        headers=headers,
    )
    if login.status_code < 400:
        logged_in = login.status_code == 204 or login.text.strip() == "Ok."

    try:
        torrents_response = await client.get(
            f"{base_url}/api/v2/torrents/info",
            params={
                "filter": "all",
                "limit": str(MAX_QB_TORRENTS + 1),
                "sort": "hash",
                "reverse": "false",
            },
            headers=headers,
        )
        torrents_response.raise_for_status()
        payload = await _read_json_response(torrents_response)
        if not isinstance(payload, list):
            raise RuntimeError("qBittorrent torrent inventory is invalid")
        truncated = len(payload) > MAX_QB_TORRENTS
        torrents = [item for item in payload[:MAX_QB_TORRENTS] if isinstance(item, dict)]

        transfer_response = await client.get(
            f"{base_url}/api/v2/transfer/info",
            headers=headers,
        )
        transfer_response.raise_for_status()
        transfer = await _read_json_response(transfer_response, limit=64 * 1024)
        if not isinstance(transfer, dict):
            raise RuntimeError("qBittorrent transfer snapshot is invalid")
        return torrents, transfer, truncated
    finally:
        if logged_in:
            try:
                await client.post(f"{base_url}/api/v2/auth/logout", headers=headers)
            except httpx.HTTPError:
                pass


def _qb_lines(
    torrents: list[dict[str, object]],
    transfer: dict[str, object],
    *,
    truncated: bool,
) -> list[str]:
    lines = [
        _metric("wos_torrent_qb_inventory_truncated", int(truncated)),
        _metric("wos_torrent_qb_torrents_total", len(torrents)),
    ]
    state_counts: dict[str, int] = {}
    active = 0
    valid_count = 0
    for torrent in torrents:
        raw_hash = torrent.get("hash")
        raw_state = torrent.get("state")
        if not isinstance(raw_hash, str) or _HASH_RE.fullmatch(raw_hash.lower()) is None:
            continue
        if not isinstance(raw_state, str) or _STATE_RE.fullmatch(raw_state) is None:
            continue
        info_hash = raw_hash.lower()
        hash8 = info_hash[:8]
        progress = _finite_number(torrent.get("progress"))
        dl_speed = _finite_number(torrent.get("dlspeed"))
        up_speed = _finite_number(torrent.get("upspeed"))
        downloaded = _finite_number(torrent.get("downloaded"))
        uploaded = _finite_number(torrent.get("uploaded"))
        if progress is None or progress > 1:
            continue

        valid_count += 1
        state_counts[raw_state] = state_counts.get(raw_state, 0) + 1
        if (dl_speed or 0) > 0 or (up_speed or 0) > 0:
            active += 1
        labels = {"hash8": hash8}
        lines.append(
            _metric(
                "wos_torrent_qb_progress_ratio",
                progress,
                {"hash8": hash8, "state": raw_state},
            )
        )
        if dl_speed is not None:
            lines.append(
                _metric("wos_torrent_qb_download_rate_bytes_per_second", dl_speed, labels)
            )
        if up_speed is not None:
            lines.append(
                _metric("wos_torrent_qb_upload_rate_bytes_per_second", up_speed, labels)
            )
        if downloaded is not None:
            lines.append(_metric("wos_torrent_qb_downloaded_bytes", downloaded, labels))
        if uploaded is not None:
            lines.append(_metric("wos_torrent_qb_uploaded_bytes", uploaded, labels))

    lines.append(_metric("wos_torrent_qb_valid_torrents", valid_count))
    lines.append(_metric("wos_torrent_qb_active_torrents", active))
    for state, count in sorted(state_counts.items()):
        lines.append(_metric("wos_torrent_qb_torrents", count, {"state": state}))

    for source, metric_name in (
        ("dl_info_speed", "wos_torrent_qb_global_download_rate_bytes_per_second"),
        ("up_info_speed", "wos_torrent_qb_global_upload_rate_bytes_per_second"),
        ("dl_info_data", "wos_torrent_qb_session_downloaded_bytes"),
        ("up_info_data", "wos_torrent_qb_session_uploaded_bytes"),
    ):
        value = _finite_number(transfer.get(source))
        if value is not None:
            lines.append(_metric(metric_name, value))
    return lines


def _newgreedy_lines(payload: object) -> list[str]:
    if not isinstance(payload, dict):
        raise RuntimeError("NewGreedy stats payload is invalid")
    lines: list[str] = []
    schema = _finite_number(payload.get("_schema_version"))
    if schema is not None:
        lines.append(_metric("wos_newgreedy_stats_schema_version", schema))

    trackers = payload.get("_tracker_cumul")
    if isinstance(trackers, dict):
        for tracker, values in sorted(trackers.items()):
            if (
                not isinstance(tracker, str)
                or not 1 <= len(tracker) <= 255
                or not isinstance(values, dict)
            ):
                continue
            upload = _finite_number(values.get("ul"))
            download = _finite_number(values.get("dl"))
            if upload is not None:
                lines.append(
                    _metric(
                        "wos_newgreedy_tracker_reported_bytes",
                        upload,
                        {"direction": "upload", "tracker": tracker},
                    )
                )
            if download is not None:
                lines.append(
                    _metric(
                        "wos_newgreedy_tracker_reported_bytes",
                        download,
                        {"direction": "download", "tracker": tracker},
                    )
                )

    hashes = [
        key
        for key, value in payload.items()
        if isinstance(key, str) and _HASH8_RE.fullmatch(key) and isinstance(value, dict)
    ]
    hashes.sort()
    truncated = len(hashes) > MAX_NEWGREEDY_TORRENTS
    hashes = hashes[:MAX_NEWGREEDY_TORRENTS]
    stalled_total = 0
    target_total = 0
    valid_total = 0

    for hash8 in hashes:
        item = payload[hash8]
        assert isinstance(item, dict)
        mode = item.get("mode")
        if not isinstance(mode, str) or not 1 <= len(mode) <= 32:
            mode = "unknown"
        stalled = item.get("stalled") is True
        target_reached = item.get("target_reached") is True
        stalled_total += int(stalled)
        target_total += int(target_reached)
        valid_total += 1
        lines.append(
            _metric(
                "wos_newgreedy_torrent_status",
                1,
                {
                    "hash8": hash8,
                    "mode": mode,
                    "stalled": str(stalled).lower(),
                    "target_reached": str(target_reached).lower(),
                },
            )
        )
        for source, metric_name in (
            ("cumul_rep_ul", "wos_newgreedy_torrent_reported_upload_bytes"),
            ("cumul_rep_dl", "wos_newgreedy_torrent_reported_download_bytes"),
            ("cumul_real_ul", "wos_newgreedy_torrent_real_upload_bytes"),
            ("ann_count", "wos_newgreedy_torrent_announces_total"),
            ("last_announce_ts", "wos_newgreedy_torrent_last_announce_timestamp_seconds"),
        ):
            value = _finite_number(item.get(source))
            if value is not None:
                lines.append(_metric(metric_name, value, {"hash8": hash8}))

    lines.extend(
        [
            _metric("wos_newgreedy_torrents_total", valid_total),
            _metric("wos_newgreedy_stalled_torrents", stalled_total),
            _metric("wos_newgreedy_target_reached_torrents", target_total),
            _metric("wos_newgreedy_inventory_truncated", int(truncated)),
        ]
    )
    return lines


async def _collect() -> str:
    lines = [
        "# HELP wos_torrent_metrics_exporter_up Exporter process health.",
        "# TYPE wos_torrent_metrics_exporter_up gauge",
        _metric("wos_torrent_metrics_exporter_up", 1),
    ]

    qb_success = 0
    try:
        secret_path = Path(
            os.environ.get("WOS_TORRENT_METRICS_SECRET_PATH", str(DEFAULT_SECRET_PATH))
        )
        specs = _load_specs(secret_path)
        unique: dict[tuple[str, str, str], DeploymentAccountSpec] = {}
        for spec in specs:
            unique[
                (
                    spec.qbittorrent_url,
                    spec.qbittorrent_username,
                    spec.qbittorrent_password.get_secret_value(),
                )
            ] = spec
        all_torrents: list[dict[str, object]] = []
        aggregate_transfer: dict[str, float] = {}
        truncated = False
        async with httpx.AsyncClient(timeout=10.0) as client:
            for spec in unique.values():
                torrents, transfer, route_truncated = await _fetch_qb_route(client, spec)
                all_torrents.extend(torrents)
                truncated = truncated or route_truncated or len(all_torrents) > MAX_QB_TORRENTS
                for key in ("dl_info_speed", "up_info_speed", "dl_info_data", "up_info_data"):
                    value = _finite_number(transfer.get(key))
                    if value is not None:
                        aggregate_transfer[key] = aggregate_transfer.get(key, 0.0) + value
        all_torrents = all_torrents[:MAX_QB_TORRENTS]
        lines.extend(_qb_lines(all_torrents, aggregate_transfer, truncated=truncated))
        qb_success = 1
    except (OSError, ValueError, RuntimeError, httpx.HTTPError):
        pass
    lines.append(_metric("wos_torrent_metrics_qb_scrape_success", qb_success))

    ng_success = 0
    try:
        stats_path = Path(
            os.environ.get(
                "WOS_TORRENT_METRICS_NEWGREEDY_STATS_PATH",
                str(DEFAULT_NEWGREEDY_STATS_PATH),
            )
        )
        payload = json.loads(stats_path.read_text(encoding="utf-8"))
        lines.extend(_newgreedy_lines(payload))
        ng_success = 1
    except (OSError, ValueError, RuntimeError):
        pass
    lines.append(_metric("wos_torrent_metrics_newgreedy_scrape_success", ng_success))
    return "\n".join(lines) + "\n"


_cache_lock = threading.Lock()
_cache_text = ""
_cache_at = 0.0


def collect_metrics() -> str:
    global _cache_at, _cache_text
    now = time.monotonic()
    with _cache_lock:
        if _cache_text and now - _cache_at < _CACHE_SECONDS:
            return _cache_text
        _cache_text = asyncio.run(_collect())
        _cache_at = now
        return _cache_text


class MetricsHandler(BaseHTTPRequestHandler):
    server_version = "WOSTorrentMetrics/1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            body = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = collect_metrics().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    port = int(os.environ.get("WOS_TORRENT_METRICS_PORT", str(DEFAULT_PORT)))
    if not 1 <= port <= 65535:
        raise SystemExit("invalid torrent metrics port")
    server = ThreadingHTTPServer(("0.0.0.0", port), MetricsHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
