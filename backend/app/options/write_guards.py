"""Sanity checks applied only to what an administrator writes.

``validate_cross_options`` runs on stored state: a rule added there can make an existing
configuration unreadable and break every route that loads the options. The guards below never
look at stored state alone. Each one fires only when one of its options is changed, so a rule
can be introduced without a data migration and an already-stored value is never rejected.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping

from app.options.registry import OptionValue
from app.options.store import OptionsValidationError

QB_GLOBAL_LIMIT_OPTION = "WOS_QB_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL"
MIN_QB_GLOBAL_LIMIT_BYTES = 1_048_576
MIN_HTTP_RATE_BYTES = 65_536
HTTP_RATE_OPTIONS = (
    "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER",
    "WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL",
)
ARCHIVE_CONCURRENCY_OPTION = "WOS_FOLDER_ARCHIVE_MAX_CONCURRENT_GLOBAL"
ARCHIVE_LEASE_CAP_OPTION = "WOS_DOWNLOAD_MAX_CONCURRENT_GLOBAL"
MIN_FREE_PERCENT_OPTION = "WOS_STORAGE_MIN_FREE_PERCENT"
WARNING_PERCENT_OPTION = "WOS_STORAGE_PRESSURE_WARNING_PERCENT"

type Values = Mapping[str, OptionValue]
type Guard = Callable[[Values, Collection[str]], None]


def _reject(message: str, field: str) -> OptionsValidationError:
    return OptionsValidationError(message, code="inconsistent_options", field=field)


def _qb_global_limit(values: Values, changed: Collection[str]) -> None:
    value = values.get(QB_GLOBAL_LIMIT_OPTION)
    if (
        QB_GLOBAL_LIMIT_OPTION in changed
        and type(value) is int
        and 0 < value < MIN_QB_GLOBAL_LIMIT_BYTES
    ):
        # Below 1 MiB/s is almost certainly a unit mistake: 5 meant as 5 MB/s would throttle
        # every torrent to a few bytes per second.
        raise _reject(
            "Le débit global qBittorrent doit valoir 0 (illimité) ou au moins 1 Mio/s.",
            QB_GLOBAL_LIMIT_OPTION,
        )


def _http_rates(values: Values, changed: Collection[str]) -> None:
    for key in HTTP_RATE_OPTIONS:
        value = values.get(key)
        if key in changed and type(value) is int and 0 < value < MIN_HTTP_RATE_BYTES:
            raise _reject(
                "Un débit de téléchargement doit valoir 0 (illimité) ou au moins 64 Kio/s.",
                key,
            )


def _archive_concurrency(values: Values, changed: Collection[str]) -> None:
    streams = values.get(ARCHIVE_CONCURRENCY_OPTION)
    admissions = values.get(ARCHIVE_LEASE_CAP_OPTION)
    if (
        {ARCHIVE_CONCURRENCY_OPTION, ARCHIVE_LEASE_CAP_OPTION}.intersection(changed)
        and type(streams) is int
        and type(admissions) is int
        and streams > admissions
    ):
        # Admissions count the waiting archives too: fewer admissions than simultaneous streams
        # would make the larger setting meaningless.
        raise _reject(
            "Les archives ZIP simultanées ne peuvent pas dépasser le plafond d’admissions ZIP.",
            ARCHIVE_CONCURRENCY_OPTION,
        )


def _storage_reserve(values: Values, changed: Collection[str]) -> None:
    reserve = values.get(MIN_FREE_PERCENT_OPTION)
    warning = values.get(WARNING_PERCENT_OPTION)
    if (
        {MIN_FREE_PERCENT_OPTION, WARNING_PERCENT_OPTION}.intersection(changed)
        and type(reserve) is int
        and type(warning) is int
        and reserve + warning > 100
    ):
        # A reserve larger than what the warning threshold leaves free would put the disk in
        # critical pressure, and refuse every new content, before any warning is shown.
        raise _reject(
            "La réserve disque minimale dépasse l’espace laissé libre par le seuil d’alerte.",
            MIN_FREE_PERCENT_OPTION,
        )


GUARDS: tuple[Guard, ...] = (
    _qb_global_limit,
    _http_rates,
    _archive_concurrency,
    _storage_reserve,
)


def validate_written_options(values: Values, changed: Collection[str]) -> None:
    """Raise when an administrator change introduces a known inconsistency.

    ``values`` is the resulting state; ``changed`` only the keys whose value actually changes.
    """
    for guard in GUARDS:
        guard(values, changed)
