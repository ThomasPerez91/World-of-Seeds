import configparser
import math
import os
import re
import secrets
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal

type ConfigValue = bool | int | float | str
type ConfigInputType = Literal["boolean", "integer", "number", "text", "select"]

CONTROL_DIRECTORY = ".wos-control"
NEWGREEDY_DIRECTORY = "newgreedy"
CONFIG_FILENAME = "config.ini"

_SECTION_RE = re.compile(r"^\s*\[([A-Za-z0-9_]+)]\s*(?:[;#].*)?$")
_OPTION_RE = re.compile(r"^(?P<prefix>\s*([A-Za-z0-9_]+)\s*=\s*)(?P<value>.*)$")
_INLINE_COMMENT_RE = re.compile(r"^(?P<value>.*?)(?P<comment>\s+[;#].*)?$")
_PORT_RANGE_RE = re.compile(r"^(\d{1,5})-(\d{1,5})$")
_HOURS_RANGE_RE = re.compile(r"^(\d{1,2})-(\d{1,2})$")
_DOMAIN_LIST_RE = re.compile(r"^[A-Za-z0-9._,*:\-\s]*$")


class NewGreedyConfigError(Exception):
    """Base error for the local NewGreedy configuration."""


class NewGreedyConfigUnavailableError(NewGreedyConfigError):
    """Raised when the fixed control path is missing or unreadable."""


class NewGreedyConfigUnsafeError(NewGreedyConfigError):
    """Raised when a path or file fails an integrity check."""


class NewGreedyConfigValidationError(NewGreedyConfigError):
    """Raised when a requested value is not supported."""


@dataclass(frozen=True, slots=True)
class ConfigFieldSpec:
    section: str
    key: str
    label: str
    description: str
    input_type: ConfigInputType
    editable: bool = True
    minimum: float | None = None
    maximum: float | None = None
    options: tuple[str, ...] = ()
    validator: Callable[[str], bool] | None = None

    @property
    def identifier(self) -> str:
        return f"{self.section}.{self.key}"


@dataclass(frozen=True, slots=True)
class ConfigFieldValue:
    spec: ConfigFieldSpec
    value: ConfigValue


def _spec(
    section: str,
    key: str,
    label: str,
    description: str,
    input_type: ConfigInputType,
    *,
    editable: bool = True,
    minimum: float | None = None,
    maximum: float | None = None,
    options: tuple[str, ...] = (),
    validator: Callable[[str], bool] | None = None,
) -> ConfigFieldSpec:
    return ConfigFieldSpec(
        section=section,
        key=key,
        label=label,
        description=description,
        input_type=input_type,
        editable=editable,
        minimum=minimum,
        maximum=maximum,
        options=options,
        validator=validator,
    )


FIELD_SPECS = (
    _spec(
        "proxy",
        "listen_port",
        "Port du proxy",
        "Port interne écouté par NewGreedy.",
        "integer",
        editable=False,
    ),
    _spec(
        "proxy",
        "tracker_timeout",
        "Délai des trackers",
        "Délai maximal d’une requête tracker, en secondes.",
        "integer",
        minimum=1,
        maximum=60,
    ),
    _spec(
        "spoofing",
        "upload_mode",
        "Mode d’upload",
        "Algorithme utilisé pour simuler l’upload.",
        "select",
        editable=False,
        options=("ratio_based",),
    ),
    _spec(
        "spoofing",
        "target_ratio",
        "Ratio cible",
        "Ratio upload/download visé.",
        "number",
        minimum=0,
        maximum=20,
    ),
    _spec(
        "spoofing",
        "target_ratio_buffer",
        "Marge du ratio",
        "Marge interne ajoutée au ratio cible.",
        "number",
        minimum=0,
        maximum=1,
    ),
    _spec(
        "spoofing",
        "anti_clustering",
        "Espacer les annonces",
        "Évite de regrouper les annonces simulées.",
        "boolean",
    ),
    _spec(
        "spoofing",
        "max_ratio_per_torrent",
        "Ratio maximal par torrent",
        "Plafond de ratio pour un torrent.",
        "number",
        minimum=0,
        maximum=100,
    ),
    _spec(
        "spoofing",
        "max_global_ratio_per_tracker",
        "Ratio maximal par tracker",
        "Plafond cumulé pour un domaine de tracker.",
        "number",
        minimum=0,
        maximum=100,
    ),
    _spec(
        "spoofing",
        "catch_up_factor",
        "Facteur de rattrapage",
        "Agressivité du rattrapage vers le ratio cible.",
        "number",
        minimum=0,
        maximum=1,
    ),
    _spec(
        "spoofing",
        "seed_credit_mb",
        "Crédit seed",
        "Mégaoctets crédités par annonce en seed pur.",
        "number",
        minimum=0,
        maximum=10000,
    ),
    _spec(
        "spoofing",
        "seed_target_mb",
        "Objectif seed",
        "Objectif d’upload simulé en mégaoctets.",
        "number",
        minimum=0,
        maximum=10000000,
    ),
    _spec(
        "spoofing",
        "seeding_dl_ratio",
        "Ratio seed réservé",
        "Paramètre réservé par NewGreedy.",
        "number",
        editable=False,
    ),
    _spec(
        "spoofing",
        "max_simulated_speed_mbps",
        "Débit simulé maximal",
        "Débit maximal simulé en Mo/s.",
        "number",
        minimum=0,
        maximum=100000,
    ),
    _spec(
        "spoofing",
        "upload_noise_pct",
        "Variation de l’upload",
        "Bruit appliqué à chaque incrément, en pourcentage.",
        "number",
        minimum=0,
        maximum=100,
    ),
    _spec(
        "spoofing",
        "stagnation_probability",
        "Probabilité de stagnation",
        "Probabilité d’une stagnation volontaire par annonce.",
        "number",
        minimum=0,
        maximum=1,
    ),
    _spec(
        "spoofing",
        "auto_stop_at_target",
        "Arrêt au ratio cible",
        "Arrête l’injection une fois le ratio cible atteint.",
        "boolean",
    ),
    _spec(
        "anti_detection",
        "user_agent_mode",
        "Mode du User-Agent",
        "Rotation automatique ou valeur fixe.",
        "select",
        options=("random", "fixed"),
    ),
    _spec(
        "anti_detection",
        "user_agent_value",
        "User-Agent fixe",
        "Valeur utilisée en mode fixe.",
        "text",
        validator=lambda value: 1 <= len(value) <= 256 and "\n" not in value and "\r" not in value,
    ),
    _spec(
        "anti_detection",
        "spoof_user_agent",
        "Simuler le User-Agent",
        "Aligne le User-Agent sur l’identifiant du pair.",
        "boolean",
    ),
    _spec(
        "anti_detection",
        "spoof_peer_id",
        "Simuler le peer ID",
        "Génère un identifiant de pair par session.",
        "boolean",
    ),
    _spec(
        "anti_detection",
        "spoof_peers",
        "Faire varier les pairs",
        "Fait varier le nombre de pairs demandé.",
        "boolean",
    ),
    _spec(
        "anti_detection",
        "peer_variance",
        "Variation des pairs",
        "Amplitude de variation du nombre de pairs.",
        "number",
        minimum=0,
        maximum=1,
    ),
    _spec(
        "anti_detection",
        "spoof_port",
        "Simuler le port",
        "Attribue un port stable par torrent.",
        "boolean",
    ),
    _spec(
        "anti_detection",
        "port_range",
        "Plage de ports",
        "Plage inclusive au format 6881-6999.",
        "text",
        validator=lambda value: _valid_port_range(value),
    ),
    _spec(
        "anti_detection",
        "spoof_headers",
        "Simuler les en-têtes",
        "Ajoute des en-têtes HTTP cohérents.",
        "boolean",
    ),
    _spec(
        "anti_detection",
        "intercept_scrape",
        "Intercepter les scrapes",
        "Ignore les requêtes scrape.",
        "boolean",
    ),
    _spec(
        "anti_detection",
        "tracker_whitelist",
        "Trackers autorisés",
        "Domaines séparés par des virgules ; vide pour tous.",
        "text",
        validator=lambda value: len(value) <= 2048 and bool(_DOMAIN_LIST_RE.fullmatch(value)),
    ),
    _spec(
        "anti_detection",
        "tracker_blacklist",
        "Trackers ignorés",
        "Domaines séparés par des virgules.",
        "text",
        validator=lambda value: len(value) <= 2048 and bool(_DOMAIN_LIST_RE.fullmatch(value)),
    ),
    _spec(
        "ssl",
        "ssl_verify_trackers",
        "Vérifier les certificats",
        "Vérifie les certificats TLS des trackers.",
        "boolean",
    ),
    _spec(
        "stats",
        "persist_stats",
        "Conserver les statistiques",
        "Enregistre les statistiques sur disque.",
        "boolean",
    ),
    _spec(
        "stats",
        "auto_purge_stopped",
        "Purger les torrents arrêtés",
        "Retire les statistiques à la réception de l’événement stopped.",
        "boolean",
    ),
    _spec(
        "web",
        "web_enabled",
        "Interface Web NewGreedy",
        "Active l’API et l’interface internes.",
        "boolean",
        editable=False,
    ),
    _spec(
        "web",
        "web_host",
        "Adresse d’écoute Web",
        "Adresse interne de l’API NewGreedy.",
        "text",
        editable=False,
    ),
    _spec(
        "web", "web_port", "Port Web", "Port interne de l’API NewGreedy.", "integer", editable=False
    ),
    _spec(
        "advanced",
        "min_announce_interval",
        "Intervalle minimal",
        "Délai minimal entre deux annonces, en secondes.",
        "integer",
        minimum=60,
        maximum=86400,
    ),
    _spec(
        "advanced",
        "log_level",
        "Niveau des logs",
        "Niveau de verbosité de NewGreedy.",
        "select",
        options=("DEBUG", "INFO", "WARNING", "ERROR"),
    ),
    _spec(
        "advanced",
        "multi_tracker_delay_min",
        "Délai tracker minimal réservé",
        "Paramètre réservé par NewGreedy.",
        "number",
        editable=False,
    ),
    _spec(
        "advanced",
        "multi_tracker_delay_max",
        "Délai tracker maximal réservé",
        "Paramètre réservé par NewGreedy.",
        "number",
        editable=False,
    ),
    _spec(
        "advanced",
        "event_anomaly_probability",
        "Probabilité d’événement",
        "Probabilité d’injecter un événement started.",
        "number",
        minimum=0,
        maximum=1,
    ),
    _spec(
        "advanced",
        "corrupt_field_probability",
        "Probabilité de corrupt",
        "Probabilité d’ajouter le champ corrupt.",
        "number",
        minimum=0,
        maximum=1,
    ),
    _spec(
        "advanced",
        "stall_announce_threshold",
        "Seuil de blocage réseau",
        "Nombre d’annonces sans download avant le signalement.",
        "integer",
        minimum=1,
        maximum=1000,
    ),
    _spec(
        "advanced",
        "min_announces_before_stagnation",
        "Seuil de stagnation",
        "Nombre minimal d’annonces avant une stagnation.",
        "integer",
        minimum=1,
        maximum=1000,
    ),
    _spec(
        "advanced",
        "interval_jitter_pct",
        "Variation de l’intervalle",
        "Variation appliquée à l’intervalle d’annonce.",
        "number",
        minimum=0,
        maximum=1,
    ),
    _spec(
        "advanced",
        "inject_hours",
        "Plage d’injection",
        "Heures inclusives au format 0-23.",
        "text",
        validator=lambda value: _valid_hours_range(value),
    ),
)
FIELD_SPECS_BY_ID = {spec.identifier: spec for spec in FIELD_SPECS}


def _valid_port_range(value: str) -> bool:
    match = _PORT_RANGE_RE.fullmatch(value)
    return match is not None and 1 <= int(match.group(1)) <= int(match.group(2)) <= 65535


def _valid_hours_range(value: str) -> bool:
    match = _HOURS_RANGE_RE.fullmatch(value)
    return match is not None and 0 <= int(match.group(1)) <= int(match.group(2)) <= 23


class NewGreedyConfigStore:
    def __init__(self, data_root: Path, *, max_bytes: int = 128 * 1024) -> None:
        self._data_root = data_root
        self._max_bytes = max_bytes
        self._lock = Lock()

    def read(self) -> list[ConfigFieldValue]:
        with self._lock:
            raw = self._read_bytes()
            return self._parse(raw)

    def update(self, changes: Mapping[str, ConfigValue]) -> list[ConfigFieldValue]:
        if not changes:
            raise NewGreedyConfigValidationError("At least one configuration value is required")
        with self._lock:
            raw = self._read_bytes()
            parsed = {field.spec.identifier: field for field in self._parse(raw)}
            normalized: dict[str, str] = {}
            for identifier, value in changes.items():
                spec = FIELD_SPECS_BY_ID.get(identifier)
                if spec is None:
                    raise NewGreedyConfigValidationError("Unknown configuration field")
                if not spec.editable:
                    raise NewGreedyConfigValidationError("This configuration field is read-only")
                normalized[identifier] = self._serialize_value(spec, value)

            if any(identifier not in parsed for identifier in normalized):
                raise NewGreedyConfigValidationError(
                    "A configuration field is missing from the file"
                )

            updated = self._replace_values(raw.decode("utf-8"), normalized).encode("utf-8")
            self._atomic_write(updated)
            return self._parse(updated)

    def _read_bytes(self) -> bytes:
        try:
            with self._control_directory_fd() as directory_fd:
                flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
                file_fd = os.open(CONFIG_FILENAME, flags, dir_fd=directory_fd)
                try:
                    file_stat = os.fstat(file_fd)
                    if not stat.S_ISREG(file_stat.st_mode):
                        raise NewGreedyConfigUnsafeError("Configuration is not a regular file")
                    if file_stat.st_mode & 0o022:
                        raise NewGreedyConfigUnsafeError("Configuration permissions are unsafe")
                    if file_stat.st_uid != os.geteuid():
                        raise NewGreedyConfigUnsafeError("Configuration ownership is unsafe")
                    if file_stat.st_size > self._max_bytes:
                        raise NewGreedyConfigUnsafeError("Configuration is too large")
                    chunks: list[bytes] = []
                    remaining = self._max_bytes + 1
                    while remaining > 0:
                        chunk = os.read(file_fd, min(64 * 1024, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    raw = b"".join(chunks)
                    if len(raw) > self._max_bytes:
                        raise NewGreedyConfigUnsafeError("Configuration is too large")
                    return raw
                finally:
                    os.close(file_fd)
        except FileNotFoundError as exc:
            raise NewGreedyConfigUnavailableError("Configuration path is missing") from exc
        except PermissionError as exc:
            raise NewGreedyConfigUnavailableError("Configuration cannot be read") from exc
        except OSError as exc:
            raise NewGreedyConfigUnsafeError("Configuration path is unsafe") from exc

    def _atomic_write(self, content: bytes) -> None:
        if len(content) > self._max_bytes:
            raise NewGreedyConfigValidationError("Configuration is too large")
        temporary_name = f".{CONFIG_FILENAME}.{secrets.token_hex(12)}.tmp"
        temporary_created = False
        try:
            with self._control_directory_fd() as directory_fd:
                original_stat = os.stat(CONFIG_FILENAME, dir_fd=directory_fd, follow_symlinks=False)
                if not stat.S_ISREG(original_stat.st_mode):
                    raise NewGreedyConfigUnsafeError("Configuration is not a regular file")
                if original_stat.st_mode & 0o022:
                    raise NewGreedyConfigUnsafeError("Configuration permissions are unsafe")
                if original_stat.st_uid != os.geteuid():
                    raise NewGreedyConfigUnsafeError("Configuration ownership is unsafe")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
                temporary_fd = os.open(
                    temporary_name,
                    flags,
                    stat.S_IMODE(original_stat.st_mode),
                    dir_fd=directory_fd,
                )
                temporary_created = True
                try:
                    view = memoryview(content)
                    while view:
                        written = os.write(temporary_fd, view)
                        if written <= 0:
                            raise OSError("Short configuration write")
                        view = view[written:]
                    os.fsync(temporary_fd)
                finally:
                    os.close(temporary_fd)

                latest_stat = os.stat(CONFIG_FILENAME, dir_fd=directory_fd, follow_symlinks=False)
                if (latest_stat.st_dev, latest_stat.st_ino) != (
                    original_stat.st_dev,
                    original_stat.st_ino,
                ):
                    raise NewGreedyConfigUnsafeError("Configuration changed concurrently")
                os.replace(
                    temporary_name,
                    CONFIG_FILENAME,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
                temporary_created = False
                os.fsync(directory_fd)
        except FileNotFoundError as exc:
            raise NewGreedyConfigUnavailableError("Configuration path is missing") from exc
        except PermissionError as exc:
            raise NewGreedyConfigUnavailableError("Configuration cannot be written") from exc
        except NewGreedyConfigError:
            raise
        except OSError as exc:
            raise NewGreedyConfigUnsafeError("Configuration write failed") from exc
        finally:
            if temporary_created:
                try:
                    with self._control_directory_fd() as directory_fd:
                        os.unlink(temporary_name, dir_fd=directory_fd)
                except OSError:
                    pass

    def _control_directory_fd(self) -> "SecureDirectoryChain":
        return SecureDirectoryChain(
            self._data_root,
            (CONTROL_DIRECTORY, NEWGREEDY_DIRECTORY),
        )

    @staticmethod
    def _parse(raw: bytes) -> list[ConfigFieldValue]:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NewGreedyConfigValidationError("Configuration is not UTF-8") from exc
        parser = configparser.ConfigParser(
            interpolation=None,
            strict=True,
            inline_comment_prefixes=(";", "#"),
        )
        try:
            parser.read_string(text)
        except configparser.Error as exc:
            raise NewGreedyConfigValidationError("Configuration is malformed") from exc

        fields: list[ConfigFieldValue] = []
        for spec in FIELD_SPECS:
            if not parser.has_option(spec.section, spec.key):
                continue
            raw_value = parser.get(spec.section, spec.key)
            fields.append(ConfigFieldValue(spec=spec, value=_parse_value(spec, raw_value)))
        return fields

    @staticmethod
    def _serialize_value(spec: ConfigFieldSpec, value: ConfigValue) -> str:
        if spec.input_type == "boolean":
            if type(value) is not bool:
                raise NewGreedyConfigValidationError("A boolean value is required")
            return "true" if value else "false"
        if spec.input_type == "integer":
            if type(value) is not int:
                raise NewGreedyConfigValidationError("An integer value is required")
            _validate_number(spec, value)
            return str(value)
        if spec.input_type == "number":
            if type(value) not in (int, float):
                raise NewGreedyConfigValidationError("A numeric value is required")
            number = float(value)
            _validate_number(spec, number)
            return format(number, ".15g")
        if not isinstance(value, str):
            raise NewGreedyConfigValidationError("A text value is required")
        if spec.options and value not in spec.options:
            raise NewGreedyConfigValidationError("The selected value is not supported")
        if spec.validator is not None and not spec.validator(value):
            raise NewGreedyConfigValidationError("The text value is invalid")
        return value

    @staticmethod
    def _replace_values(text: str, changes: Mapping[str, str]) -> str:
        current_section: str | None = None
        replaced: set[str] = set()
        output: list[str] = []
        for line in text.splitlines(keepends=True):
            body = line.rstrip("\r\n")
            newline = line[len(body) :]
            section_match = _SECTION_RE.fullmatch(body)
            if section_match is not None:
                current_section = section_match.group(1).lower()
                output.append(line)
                continue
            option_match = _OPTION_RE.fullmatch(body)
            if option_match is None or current_section is None:
                output.append(line)
                continue
            key = option_match.group(2).lower()
            identifier = f"{current_section}.{key}"
            if identifier not in changes:
                output.append(line)
                continue
            if identifier in replaced:
                raise NewGreedyConfigValidationError("Configuration contains a duplicate field")
            value_match = _INLINE_COMMENT_RE.fullmatch(option_match.group("value"))
            comment = value_match.group("comment") if value_match is not None else None
            output.append(
                f"{option_match.group('prefix')}{changes[identifier]}{comment or ''}{newline}"
            )
            replaced.add(identifier)
        if replaced != set(changes):
            raise NewGreedyConfigValidationError("A configuration field is missing from the file")
        return "".join(output)


def _parse_value(spec: ConfigFieldSpec, raw_value: str) -> ConfigValue:
    value = raw_value.strip()
    try:
        if spec.input_type == "boolean":
            lowered = value.lower()
            if lowered in configparser.ConfigParser.BOOLEAN_STATES:
                return configparser.ConfigParser.BOOLEAN_STATES[lowered]
            raise ValueError
        if spec.input_type == "integer":
            parsed_integer = int(value)
            _validate_number(spec, parsed_integer)
            return parsed_integer
        if spec.input_type == "number":
            parsed_number = float(value)
            _validate_number(spec, parsed_number)
            return parsed_number
        if spec.options and value not in spec.options:
            raise ValueError
        if spec.validator is not None and not spec.validator(value):
            raise ValueError
        return value
    except (TypeError, ValueError) as exc:
        raise NewGreedyConfigValidationError(
            f"Invalid value for configuration field {spec.identifier}"
        ) from exc


def _validate_number(spec: ConfigFieldSpec, value: int | float) -> None:
    number = float(value)
    if not math.isfinite(number):
        raise NewGreedyConfigValidationError("Numeric values must be finite")
    if spec.minimum is not None and number < spec.minimum:
        raise NewGreedyConfigValidationError("Numeric value is below the allowed minimum")
    if spec.maximum is not None and number > spec.maximum:
        raise NewGreedyConfigValidationError("Numeric value is above the allowed maximum")


class SecureDirectoryChain:
    def __init__(self, root: Path, components: tuple[str, ...]) -> None:
        self._root = root
        self._components = components
        self._fd: int | None = None

    def __enter__(self) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        current_fd = os.open(self._root, flags)
        try:
            self._validate(current_fd)
            for component in self._components:
                next_fd = os.open(component, flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
                self._validate(current_fd)
            self._fd = current_fd
            return current_fd
        except Exception:
            os.close(current_fd)
            raise

    @staticmethod
    def _validate(directory_fd: int) -> None:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise NewGreedyConfigUnsafeError("Control path is not a directory")
        if directory_stat.st_mode & 0o022:
            raise NewGreedyConfigUnsafeError("Control directory permissions are unsafe")
        if directory_stat.st_uid != os.geteuid():
            raise NewGreedyConfigUnsafeError("Control directory ownership is unsafe")

    def __exit__(self, *_: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
