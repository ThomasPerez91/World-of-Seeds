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


READY_WITH_FOLDERS = TORRENTS[3]["id"]
SNAPSHOT = "00000000-0000-4000-a000-000000000001"


def _file(index: int, relative_path: str, size_gib: float) -> dict[str, Any]:
    return {
        "id": f"00000000-0000-4000-b000-{index:012d}",
        "file_index": index,
        "relative_path": relative_path,
        "size": int(size_gib * GIB),
    }


FILES = [
    _file(0, "Lisez-moi.txt", 0.001),
    _file(1, "Chapitre 01 - Bases/01 - Variables.mp4", 1.2),
    _file(2, "Chapitre 01 - Bases/02 - Fonctions.mp4", 1.4),
    _file(3, "Chapitre 02 - Projets/01 - API web.mp4", 3.6),
    _file(4, "Chapitre 02 - Projets/02 - Tests.mp4", 3.5),
]


def _manifest() -> dict[str, Any]:
    return {
        "snapshot_id": SNAPSHOT,
        "manifest_version": 1,
        "file_count": len(FILES),
        "total_size": sum(f["size"] for f in FILES),
        "archive_available": True,
        "retention_expires_at": _at(days=2),
        "offset": 0,
        "limit": 50,
        "items": FILES,
    }


def _directories() -> dict[str, Any]:
    def folder(name: str) -> dict[str, Any]:
        inside = [f for f in FILES if f["relative_path"].startswith(name + "/")]
        return {
            "name": name,
            "relative_path": name,
            "file_count": len(inside),
            "total_size": sum(f["size"] for f in inside),
            "archive_available": True,
        }

    return {
        "snapshot_id": SNAPSHOT,
        "path": "",
        "directories": [folder("Chapitre 01 - Bases"), folder("Chapitre 02 - Projets")],
        "direct_file_count": 1,
        "offset": 0,
        "limit": 500,
        "files": [FILES[0]],
    }


def _option(
    key: str, label: str, input_type: str, value: Any, *, unit: str | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "description": f"Description de {label.lower()}.",
        "input_type": input_type,
        "value": value,
        "default": value,
        "unit": unit,
        "minimum": 0 if input_type == "integer" else None,
        "maximum": None,
        "choices": [],
        "editable": True,
        "restart_required": False,
        "version": 1,
        **extra,
    }


def _admin_overview() -> dict[str, Any]:
    return {
        "sections": [
            {
                "id": "downloads",
                "label": "Téléchargements",
                "fields": [
                    _option(
                        "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER",
                        "Débit",
                        "integer",
                        0,
                        unit="bytes_per_second",
                    ),
                    _option(
                        "WOS_DOWNLOAD_MAX_CONCURRENT_PER_USER",
                        "Archives",
                        "integer",
                        2,
                        unit="count",
                    ),
                    _option(
                        "WOS_TORRENT_AUTO_UNSUBSCRIBE_HOURS",
                        "Désabonnement",
                        "integer",
                        336,
                        unit="hours",
                        restart_required=True,
                    ),
                ],
            },
            {
                "id": "torrents",
                "label": "Torrents",
                "fields": [
                    _option(
                        "WOS_SCHEDULER_DYNAMIC_CONCURRENCY_ENABLED", "Régulation", "boolean", True
                    ),
                    _option(
                        "WOS_SCHEDULER_MAX_ACTIVE_GLOBAL", "Actifs", "integer", 12, unit="count"
                    ),
                ],
            },
            {
                "id": "security",
                "label": "Sécurité",
                "fields": [
                    _option(
                        "WOS_REQUEST_RATE_LIMIT_PER_MINUTE",
                        "Limiteur",
                        "integer",
                        300,
                        unit="count_per_minute",
                    ),
                ],
            },
        ],
        "changed_keys": [],
        "restart_required": False,
        "service_controls_available": True,
        "scheduler": {
            "desired_generation": 42,
            "applied_generation": 42,
            "synchronized": True,
            "rounds": 1840,
            "lease_active": True,
            "dynamic_enabled": True,
            "static_active_limit": 12,
            "dynamic_current_active": 5,
            "active_downloads": 2,
            "waiting_candidates": 1,
            "observed_bytes_per_second": 48_300_000,
            "minimum_bytes_per_second": 20_000_000,
            "target_bytes_per_second": 90_000_000,
            "next_evaluation_at": _at(minutes=3),
            "completion_cooldown_until": None,
            "last_decision": "hold",
        },
        "storage": {
            "managed_bytes": 2600 * GIB,
            "logical_bytes": 2750 * GIB,
            "disk_total_bytes": 4000 * GIB,
            "disk_free_bytes": 1130 * GIB,
            "pressure": "normal",
            "managed_quota_bytes": 3500 * GIB,
            "user_quota_bytes": 0,
        },
        "audit": [
            {
                "key": "WOS_REQUEST_RATE_LIMIT_PER_MINUTE",
                "version": 2,
                "old_value": 60,
                "new_value": 300,
                "actor": None,
                "source": "migration",
                "changed_at": _at(hours=-6),
            },
            {
                "key": "WOS_SCHEDULER_MAX_ACTIVE_GLOBAL",
                "version": 3,
                "old_value": 10,
                "new_value": 12,
                "actor": "thomas",
                "source": "admin",
                "changed_at": _at(days=-3),
            },
        ],
    }


def _qbittorrent_runtime() -> dict[str, Any]:
    return {
        "checked_at": _at(seconds=-10),
        "download_speed_bytes": 48_300_000,
        "upload_speed_bytes": 12_700_000,
        "truncated": False,
        "torrents": [
            {
                "hash": f"{index:040x}",
                "name": torrent["name"],
                "size_bytes": torrent["total_size"],
                "state": {"active": "downloading", "ready": "uploading", "error": "error"}.get(
                    torrent["state"], "queuedDL"
                ),
                "progress": torrent["progress"],
                "managed_torrent_id": torrent["id"],
                "can_resume": torrent["state"] == "error",
                "can_delete": True,
            }
            for index, torrent in enumerate(TORRENTS, start=1)
        ],
    }


def _cleanup() -> dict[str, Any]:
    return {
        "checked_at": _at(seconds=-30),
        "items": [
            {
                "id": TORRENTS[3]["id"],
                "name": TORRENTS[3]["name"],
                "size_bytes": TORRENTS[3]["total_size"],
                "subscriber_count": 1,
                "deletion_at": _at(days=2),
            },
            {
                "id": TORRENTS[2]["id"],
                "name": TORRENTS[2]["name"],
                "size_bytes": TORRENTS[2]["total_size"],
                "subscriber_count": 0,
                "deletion_at": None,
            },
        ],
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
        ("GET", "/api/v1/admin/storage"): (
            200,
            {
                "total": 4000 * GIB,
                "used": 2870 * GIB,
                "available": 1130 * GIB,
                "active_users": 2,
                "suspended_users": 1,
            },
        ),
        ("GET", "/api/v2/admin/overview"): (200, _admin_overview()),
        ("GET", "/api/v1/admin/services/wos/restart"): (
            200,
            {"state": "idle", "request_id": None, "updated_at": None, "message_code": "idle"},
        ),
        ("GET", "/api/v2/admin/runtime/qbittorrent"): (200, _qbittorrent_runtime()),
        ("GET", "/api/v2/admin/cleanup"): (200, _cleanup()),
        ("GET", f"/api/v2/torrents/{READY_WITH_FOLDERS}/download-manifest"): (200, _manifest()),
        ("GET", f"/api/v2/torrents/{READY_WITH_FOLDERS}/download-directories"): (
            200,
            _directories(),
        ),
    }
    return routes.get((method, path))
