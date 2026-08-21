from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type OptionValue = bool | int | str
type OptionInputType = Literal["boolean", "integer", "select"]
type OptionCategory = Literal[
    "downloads",
    "torrents",
    "storage",
    "retention",
    "cache",
    "performance",
    "security",
    "interface",
]

CATEGORY_LABELS: dict[OptionCategory, str] = {
    "downloads": "Téléchargements",
    "torrents": "Torrents",
    "storage": "Stockage",
    "retention": "Rétention",
    "cache": "Cache",
    "performance": "Performance",
    "security": "Sécurité fonctionnelle",
    "interface": "Interface",
}

_SENSITIVE_KEY_FRAGMENTS = (
    "PASSWORD",
    "TOKEN",
    "PASSKEY",
    "SECRET",
    "PRIVATE_KEY",
    "CREDENTIAL",
)


def is_sensitive_option_key(key: str) -> bool:
    return any(fragment in key.upper() for fragment in _SENSITIVE_KEY_FRAGMENTS)


@dataclass(frozen=True, slots=True)
class OptionSpec:
    key: str
    label: str
    description: str
    input_type: OptionInputType
    default: OptionValue
    category: OptionCategory
    unit: str | None = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    editable: bool = True
    restart_required: bool = False
    sensitive: bool = False


def _integer(
    key: str,
    label: str,
    description: str,
    default: int,
    category: OptionCategory,
    *,
    minimum: int,
    maximum: int,
    unit: str,
    editable: bool = True,
    restart_required: bool = False,
) -> OptionSpec:
    return OptionSpec(
        key=key,
        label=label,
        description=description,
        input_type="integer",
        default=default,
        category=category,
        unit=unit,
        minimum=minimum,
        maximum=maximum,
        editable=editable,
        restart_required=restart_required,
    )


OPTION_SPECS: tuple[OptionSpec, ...] = (
    _integer(
        "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER",
        "Débit maximal par utilisateur",
        "Plafond de téléchargement HTTP par utilisateur ; 0 désactive la limite.",
        0,
        "downloads",
        minimum=0,
        maximum=1_000_000_000,
        unit="bytes_per_second",
    ),
    _integer(
        "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL",
        "Débit maximal global",
        "Plafond cumulé des téléchargements HTTP ; 0 désactive la limite.",
        0,
        "downloads",
        minimum=0,
        maximum=10_000_000_000,
        unit="bytes_per_second",
    ),
    _integer(
        "WOS_DOWNLOAD_MAX_CONCURRENT_PER_USER",
        "Téléchargements simultanés par utilisateur",
        "Nombre maximal de flux de fichiers ouverts par un même compte.",
        2,
        "downloads",
        minimum=1,
        maximum=20,
        unit="count",
    ),
    _integer(
        "WOS_DOWNLOAD_LEASE_SECONDS",
        "Durée d’une lease",
        "Durée de protection d’un contenu pendant son téléchargement.",
        120,
        "downloads",
        minimum=30,
        maximum=3600,
        unit="seconds",
    ),
    _integer(
        "WOS_FOLDER_ARCHIVE_MAX_BYTES",
        "Taille maximale d’une archive dossier",
        "Volume source maximal accepté pour un téléchargement ZIP de dossier.",
        274_877_906_944,
        "downloads",
        minimum=1_048_576,
        maximum=1_099_511_627_776,
        unit="bytes",
    ),
    _integer(
        "WOS_TORRENT_MAX_ACTIVE_PER_USER",
        "Torrents actifs par utilisateur",
        "Nombre maximal de demandes actives pour un même compte.",
        5,
        "torrents",
        minimum=1,
        maximum=100,
        unit="count",
    ),
    _integer(
        "WOS_TORRENT_MAX_SIZE_BYTES",
        "Taille maximale d’un contenu",
        "Taille totale maximale acceptée après analyse du fichier .torrent.",
        1_099_511_627_776,
        "torrents",
        minimum=1_048_576,
        maximum=10_995_116_277_760,
        unit="bytes",
    ),
    _integer(
        "WOS_TORRENT_UPLOAD_MAX_FILE_BYTES",
        "Taille maximale du fichier .torrent",
        "Taille HTTP maximale du manifeste bencode déposé.",
        4_194_304,
        "torrents",
        minimum=65_536,
        maximum=33_554_432,
        unit="bytes",
    ),
    _integer(
        "WOS_QB_SYNC_INTERVAL_SECONDS",
        "Intervalle de synchronisation qBittorrent",
        "Délai entre deux synchronisations centralisées du worker.",
        5,
        "torrents",
        minimum=2,
        maximum=300,
        unit="seconds",
    ),
    _integer(
        "WOS_TORRENT_MAX_RETRY_ATTEMPTS",
        "Tentatives maximales",
        "Nombre de reprises automatiques avant passage en erreur durable.",
        5,
        "torrents",
        minimum=0,
        maximum=20,
        unit="count",
    ),
    _integer(
        "WOS_TORRENT_RETRY_BASE_SECONDS",
        "Base du délai de reprise",
        "Délai initial du backoff exponentiel du worker.",
        30,
        "torrents",
        minimum=1,
        maximum=3600,
        unit="seconds",
    ),
    _integer(
        "WOS_WORKER_CONCURRENCY",
        "Concurrence du worker",
        "Nombre d’opérations torrent traitées en parallèle par le futur worker.",
        2,
        "performance",
        minimum=1,
        maximum=16,
        unit="count",
        restart_required=True,
    ),
    _integer(
        "WOS_TORRENT_RETENTION_HOURS",
        "Rétention des contenus prêts",
        "Durée minimale avant qu’un contenu sans référence devienne purgeable.",
        48,
        "retention",
        minimum=1,
        maximum=2160,
        unit="hours",
    ),
    _integer(
        "WOS_STORAGE_MANAGED_MAX_BYTES",
        "Espace total géré",
        "Plafond des contenus partagés WOS ; 0 utilise uniquement la réserve disque.",
        0,
        "storage",
        minimum=0,
        maximum=10_995_116_277_760,
        unit="bytes",
    ),
    _integer(
        "WOS_STORAGE_USER_MAX_BYTES",
        "Quota logique par utilisateur",
        "Volume maximal référencé par utilisateur ; 0 désactive ce quota.",
        0,
        "storage",
        minimum=0,
        maximum=10_995_116_277_760,
        unit="bytes",
    ),
    _integer(
        "WOS_STORAGE_MIN_FREE_BYTES",
        "Réserve disque minimale",
        "Espace qui doit rester libre avant l’acceptation d’une nouvelle demande.",
        10_737_418_240,
        "storage",
        minimum=0,
        maximum=1_099_511_627_776,
        unit="bytes",
    ),
    _integer(
        "WOS_STORAGE_MIN_FREE_PERCENT",
        "Réserve disque minimale en pourcentage",
        "Pourcentage du disque qui doit rester libre.",
        10,
        "storage",
        minimum=0,
        maximum=90,
        unit="percent",
    ),
    _integer(
        "WOS_STORAGE_PRESSURE_WARNING_PERCENT",
        "Seuil d’alerte stockage",
        "Occupation à partir de laquelle l’administration affiche un avertissement.",
        80,
        "storage",
        minimum=1,
        maximum=98,
        unit="percent",
    ),
    _integer(
        "WOS_STORAGE_PRESSURE_CRITICAL_PERCENT",
        "Seuil critique stockage",
        "Occupation à partir de laquelle les nouvelles demandes sont bloquées.",
        90,
        "storage",
        minimum=2,
        maximum=99,
        unit="percent",
    ),
    _integer(
        "WOS_CACHE_DEFAULT_TTL_SECONDS",
        "TTL cache par défaut",
        "Durée des entrées Redis non liées à la progression.",
        300,
        "cache",
        minimum=5,
        maximum=86_400,
        unit="seconds",
    ),
    _integer(
        "WOS_CACHE_PROGRESS_TTL_SECONDS",
        "TTL des progressions",
        "Durée courte des snapshots de progression torrent.",
        10,
        "cache",
        minimum=2,
        maximum=300,
        unit="seconds",
    ),
    _integer(
        "WOS_CACHE_CONNECTION_POOL_SIZE",
        "Connexions Redis",
        "Taille du pool Redis utilisé par l’API et le worker.",
        10,
        "cache",
        minimum=1,
        maximum=100,
        unit="count",
        restart_required=True,
    ),
    _integer(
        "WOS_REQUEST_RATE_LIMIT_PER_MINUTE",
        "Requêtes métier par minute",
        "Limite fonctionnelle appliquée à un compte authentifié.",
        60,
        "security",
        minimum=10,
        maximum=1000,
        unit="count_per_minute",
    ),
    _integer(
        "WOS_FILES_LIST_MAX_ENTRIES",
        "Éléments par dossier",
        "Plafond de sécurité d’un listing filesystem.",
        5000,
        "performance",
        minimum=100,
        maximum=20_000,
        unit="count",
    ),
    _integer(
        "WOS_DIRECTORY_SIZE_MAX_ENTRIES",
        "Ancien budget de calcul des tailles",
        "Option conservée pour compatibilité ; les tailles de dossiers ne sont plus calculées.",
        50_000,
        "performance",
        minimum=1000,
        maximum=250_000,
        unit="count",
        editable=False,
    ),
    _integer(
        "WOS_DIRECTORY_SIZE_CACHE_SECONDS",
        "Ancien cache des tailles de dossiers",
        "Option conservée pour compatibilité ; aucun scan récursif n’est mis en cache.",
        30,
        "performance",
        minimum=1,
        maximum=3600,
        unit="seconds",
        editable=False,
    ),
    _integer(
        "WOS_HTTP_STREAM_CHUNK_BYTES",
        "Taille des blocs HTTP",
        "Taille mémoire maximale d’un bloc lors du streaming de fichiers.",
        1_048_576,
        "performance",
        minimum=65_536,
        maximum=8_388_608,
        unit="bytes",
    ),
    _integer(
        "WOS_ADMIN_REFRESH_INTERVAL_SECONDS",
        "Rafraîchissement de l’administration",
        "Délai par défaut entre deux actualisations automatiques.",
        15,
        "interface",
        minimum=5,
        maximum=300,
        unit="seconds",
    ),
)

OPTION_SPECS_BY_KEY = {spec.key: spec for spec in OPTION_SPECS}

if len(OPTION_SPECS_BY_KEY) != len(OPTION_SPECS):
    raise RuntimeError("Option registry contains duplicate keys")
if any(spec.sensitive for spec in OPTION_SPECS):
    raise RuntimeError("Functional options must never be marked sensitive")
