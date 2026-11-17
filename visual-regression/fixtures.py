"""Simulated API answers for the visual regression screens.

Every timestamp is relative to NOW, the time the browser clock is frozen at, so relative dates
("il y a 3 h") render identically on every run. Unknown endpoints answer 404 and are listed in
the report: extend this file when a screen starts calling a new endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
GIB = 1024**3


def _at(**delta: float) -> str:
    return (NOW + timedelta(**delta)).isoformat().replace("+00:00", "Z")


ADMIN = {
    "id": "00000000-0000-4000-8000-000000000001",
    "username": "thomas",
    "is_admin": True,
    "is_active": True,
    "must_change_credentials": False,
    "preferred_locale": "fr",
}

USERS = [
    {**ADMIN, "created_at": _at(days=-120), "last_login_at": _at(minutes=-5)},
    {
        "id": "00000000-0000-4000-8000-000000000002",
        "username": "camille",
        "is_admin": False,
        "is_active": True,
        "must_change_credentials": False,
        "preferred_locale": "fr",
        "created_at": _at(days=-40),
        "last_login_at": _at(days=-2, hours=-3),
    },
    {
        "id": "00000000-0000-4000-8000-000000000003",
        "username": "invite-suspendu",
        "is_admin": False,
        "is_active": False,
        "must_change_credentials": True,
        "preferred_locale": "en",
        "created_at": _at(days=-9),
        "last_login_at": None,
    },
]


def _torrent(
    index: int, name: str, size_gib: float, state: str, progress: float, **extra: Any
) -> dict[str, Any]:
    return {
        "id": f"00000000-0000-4000-9000-{index:012d}",
        "name": name,
        "total_size": int(size_gib * GIB),
        "state": state,
        "progress": progress,
        "error_code": None,
        "ready_at": None,
        "unsubscribe_at": None,
        "retention_expires_at": None,
        "queue_position_estimate": None,
        "queue_total_estimate": None,
        "queue_status": None,
        "scheduler_retry_at": None,
        "created_at": _at(hours=-index * 5),
        "updated_at": _at(minutes=-index * 7),
        **extra,
    }


TORRENTS = [
    _torrent(
        1, "Archive.Documentaires.Nature.2026", 18.4, "active", 0.62, queue_status="downloading"
    ),
    _torrent(
        2,
        "Collection.Musique.Libre.FLAC",
        3.2,
        "requested",
        0.0,
        queue_status="waiting",
        queue_position_estimate=3,
        queue_total_estimate=7,
    ),
    _torrent(
        3,
        "Distribution.Linux.Edition.Serveur.iso",
        2.1,
        "ready",
        1.0,
        ready_at=_at(hours=-20),
        unsubscribe_at=_at(days=12),
        retention_expires_at=_at(days=12),
    ),
    _torrent(
        4,
        "Cours.Video.Programmation.Python",
        9.7,
        "ready",
        1.0,
        ready_at=_at(days=-25),
        unsubscribe_at=_at(days=2),
        retention_expires_at=_at(days=2),
    ),
    _torrent(
        5,
        "Jeu.Libre.Version.Complete",
        6.5,
        "active",
        0.18,
        queue_status="stalled",
        scheduler_retry_at=_at(minutes=12),
    ),
    _torrent(6, "Paquet.Polices.Open.Source", 0.4, "error", 0.0, error_code="tracker_unreachable"),
]


def _samples(base: int, step: int) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": _at(seconds=-5 * (29 - i)),
            "value_bytes_per_second": base + (i * step) % (base // 2),
        }
        for i in range(30)
    ]


def _listing() -> dict[str, Any]:
    return {
        "items": TORRENTS,
        "offset": 0,
        "limit": 50,
        "total": len(TORRENTS),
        "status_counts": {"all": 6, "downloading": 2, "ready": 2, "waiting": 1, "blocked": 1},
        "retention_counts": {"green": 1, "orange": 0, "red": 1},
    }


def answer(scenario: str, method: str, path: str) -> tuple[int, Any] | None:
    """(status, JSON body) for an API call, or None when the endpoint is not simulated."""
    authenticated = scenario != "login" and scenario != "legal"
    routes: dict[tuple[str, str], tuple[int, Any]] = {
        ("GET", "/api/v1/health/status"): (200, {"status": "ok", "checked_at": _at(seconds=-20)}),
        ("GET", "/api/v1/auth/me"): (
            (200, {"user": ADMIN})
            if authenticated
            else (
                401,
                {
                    "detail": {
                        "code": "not_authenticated",
                        "message": "Not authenticated",
                        "field": None,
                    }
                },
            )
        ),
        ("GET", "/api/v1/auth/auth-seed"): (200, {"auth_seed": "Xq7Lm2Pz9Rt4Vb8Nc3Kd6Hj5W"}),
        ("GET", "/api/v1/admin/users"): (200, USERS),
        ("GET", "/api/v1/admin/users/quota"): (200, {"used": 3, "maximum": 25, "reached": False}),
        ("GET", "/api/v2/storage"): (
            200,
            {"total_bytes": 4000 * GIB, "used_bytes": 2870 * GIB, "available_bytes": 1130 * GIB},
        ),
        ("GET", "/api/v2/dashboard/network-throughput"): (
            200,
            {
                "status": "ok",
                "period": "realtime",
                "sample_interval_seconds": 5,
                "download": {
                    "current_bytes_per_second": 48_300_000,
                    "samples": _samples(40_000_000, 3_100_000),
                },
                "upload": {
                    "current_bytes_per_second": 12_700_000,
                    "samples": _samples(10_000_000, 1_700_000),
                },
            },
        ),
        ("GET", "/api/v2/torrents"): (200, _listing()),
    }
    return routes.get((method, path))
