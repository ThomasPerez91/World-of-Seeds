import re
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from app.integrations.http import IntegrationRequestError, read_limited_json
from app.integrations.types import (
    NewGreedyOverview,
    NewGreedyStatsReset,
    NewGreedyTorrent,
    ServiceProbe,
)


class NewGreedyClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def probe(self) -> ServiceProbe:
        started_at = perf_counter()
        try:
            async with self._client.stream("GET", f"{self._base_url}/api/health") as response:
                response.raise_for_status()
                payload = await read_limited_json(response, max_bytes=256 * 1024)
            if not isinstance(payload, dict) or not isinstance(payload.get("total"), int):
                raise IntegrationRequestError("NewGreedy health payload is invalid")
        except (httpx.HTTPError, IntegrationRequestError):
            return ServiceProbe(
                service="newgreedy",
                state="unavailable",
                latency_ms=_latency_ms(started_at),
                error_code="request_failed",
            )
        return ServiceProbe(
            service="newgreedy",
            state="healthy",
            latency_ms=_latency_ms(started_at),
        )

    async def overview(self) -> NewGreedyOverview:
        payload = await self._stats_payload()

        total_downloaded = 0.0
        total_reported_uploaded = 0.0
        total_real_uploaded = 0.0
        downloading = 0
        seeding = 0
        stalled = 0
        target_reached = 0
        for info_hash, raw_entry in payload.items():
            if not isinstance(info_hash, str) or not isinstance(raw_entry, dict):
                raise IntegrationRequestError("NewGreedy stats payload is invalid")
            downloaded = _safe_counter(raw_entry.get("cumul_rep_dl", 0))
            reported_uploaded = _safe_counter(raw_entry.get("cumul_rep_ul", 0))
            real_uploaded = _safe_counter(raw_entry.get("cumul_real_ul", 0))
            total_downloaded += downloaded
            total_reported_uploaded += reported_uploaded
            total_real_uploaded += real_uploaded
            mode = raw_entry.get("mode")
            if mode == "down":
                downloading += 1
            else:
                seeding += 1
            if raw_entry.get("stalled") is True:
                stalled += 1
            if raw_entry.get("target_reached") is True:
                target_reached += 1

        return NewGreedyOverview(
            torrents=len(payload),
            downloading=downloading,
            seeding=seeding,
            stalled=stalled,
            target_reached=target_reached,
            total_downloaded_bytes=round(total_downloaded),
            total_reported_uploaded_bytes=round(total_reported_uploaded),
            total_fake_uploaded_bytes=round(max(0, total_reported_uploaded - total_real_uploaded)),
        )

    async def torrents(self) -> list[NewGreedyTorrent]:
        payload = await self._stats_payload()
        result: list[NewGreedyTorrent] = []
        for info_hash, raw_entry in payload.items():
            if not re.fullmatch(r"[0-9a-fA-F]{8,40}", info_hash):
                raise IntegrationRequestError("NewGreedy torrent identifier is invalid")
            downloaded = _safe_counter(raw_entry.get("cumul_rep_dl", 0))
            reported_uploaded = _safe_counter(raw_entry.get("cumul_rep_ul", 0))
            real_uploaded = _safe_counter(raw_entry.get("cumul_real_ul", 0))
            announce_count = raw_entry.get("ann_count", 0)
            if type(announce_count) is not int or announce_count < 0:
                raise IntegrationRequestError("NewGreedy announce counter is invalid")
            mode = raw_entry.get("mode", "seed" if downloaded == 0 else "down")
            if mode not in ("down", "seed"):
                raise IntegrationRequestError("NewGreedy torrent mode is invalid")
            last_announce = _optional_timestamp(raw_entry.get("last_announce_ts", 0))
            result.append(
                NewGreedyTorrent(
                    id=info_hash.lower(),
                    mode=mode,
                    downloaded_bytes=round(downloaded),
                    reported_uploaded_bytes=round(reported_uploaded),
                    fake_uploaded_bytes=round(max(0, reported_uploaded - real_uploaded)),
                    ratio=round(reported_uploaded / downloaded, 4) if downloaded > 0 else None,
                    announce_count=announce_count,
                    stalled=raw_entry.get("stalled") is True,
                    target_reached=raw_entry.get("target_reached") is True,
                    last_announce_at=last_announce,
                )
            )
        return sorted(
            result,
            key=lambda torrent: torrent.last_announce_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    async def reset_stats(self) -> NewGreedyStatsReset:
        payload = await self._request_json(
            "DELETE",
            "/api/stats/purge",
            params={"keep_active": "false", "inactive_hours": "0"},
            max_bytes=256 * 1024,
        )
        if not isinstance(payload, dict):
            raise IntegrationRequestError("NewGreedy purge payload is invalid")
        purged = payload.get("purged")
        remaining = payload.get("remaining")
        if type(purged) is not int or type(remaining) is not int or purged < 0 or remaining < 0:
            raise IntegrationRequestError("NewGreedy purge payload is invalid")
        return NewGreedyStatsReset(purged=purged, remaining=remaining)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        max_bytes: int,
    ) -> Any:
        try:
            async with self._client.stream(
                method,
                f"{self._base_url}{path}",
                params=params,
            ) as response:
                response.raise_for_status()
                return await read_limited_json(response, max_bytes=max_bytes)
        except httpx.HTTPError as exc:
            raise IntegrationRequestError("NewGreedy request failed") from exc

    async def _stats_payload(self) -> dict[str, dict[str, object]]:
        payload = await self._request_json("GET", "/api/stats", max_bytes=8 * 1024 * 1024)
        if not isinstance(payload, dict) or len(payload) > 10_000:
            raise IntegrationRequestError("NewGreedy stats payload is invalid")
        result: dict[str, dict[str, object]] = {}
        for info_hash, raw_entry in payload.items():
            if not isinstance(info_hash, str) or not isinstance(raw_entry, dict):
                raise IntegrationRequestError("NewGreedy stats payload is invalid")
            result[info_hash] = raw_entry
        return result


def _latency_ms(started_at: float) -> int:
    return max(0, round((perf_counter() - started_at) * 1000))


def _safe_counter(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IntegrationRequestError("NewGreedy counter is invalid")
    result = float(value)
    if result < 0 or result != result or result in (float("inf"), float("-inf")):
        raise IntegrationRequestError("NewGreedy counter is invalid")
    return result


def _optional_timestamp(value: object) -> datetime | None:
    if value in (None, 0, 0.0):
        return None
    timestamp = _safe_counter(value)
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise IntegrationRequestError("NewGreedy timestamp is invalid") from exc
