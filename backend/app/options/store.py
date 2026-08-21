from __future__ import annotations

import os
import re
import secrets
import stat
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from app.integrations.newgreedy_config import (
    CONTROL_DIRECTORY,
    NewGreedyConfigUnsafeError,
    SecureDirectoryChain,
)
from app.options.registry import (
    OPTION_SPECS,
    OPTION_SPECS_BY_KEY,
    OptionSpec,
    OptionValue,
    is_sensitive_option_key,
)

OPTIONS_FILENAME = ".options"
BACKUP_FILENAME = ".options.backup"
MAX_OPTIONS_BYTES = 64 * 1024
MAX_OPTION_LINES = 256
_KEY_RE = re.compile(r"^WOS_[A-Z0-9_]{1,96}$")


class OptionsError(Exception):
    """Base error for functional options."""


class OptionsUnavailableError(OptionsError):
    """Raised when the fixed control directory is unavailable."""


class OptionsUnsafeError(OptionsError):
    """Raised when a file or path fails an integrity check."""


class OptionsValidationError(OptionsError):
    def __init__(self, message: str, *, code: str = "invalid_option", field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


@dataclass(frozen=True, slots=True)
class OptionFieldValue:
    spec: OptionSpec
    value: OptionValue


@dataclass(frozen=True, slots=True)
class OptionsUpdate:
    fields: list[OptionFieldValue]
    changed_keys: tuple[str, ...]
    restart_required: bool


type FileIdentity = tuple[int, int, int, int]


class OptionsStore:
    def __init__(self, data_root: Path, *, max_bytes: int = MAX_OPTIONS_BYTES) -> None:
        self._data_root = data_root
        self._max_bytes = max_bytes
        self._lock = Lock()

    def read(self) -> list[OptionFieldValue]:
        with self._lock:
            raw, _ = self._read_optional()
            values = self._values(raw)
            return self._fields(values)

    def snapshot(self) -> dict[str, OptionValue]:
        """Return one validated, internally consistent runtime snapshot."""
        with self._lock:
            raw, _ = self._read_optional()
            return self._values(raw)

    def update(self, changes: Mapping[str, OptionValue]) -> OptionsUpdate:
        if not changes:
            raise OptionsValidationError("Au moins une option doit être modifiée.")
        with self._lock:
            raw, identity = self._read_optional()
            values = self._values(raw)
            changed_keys: list[str] = []
            for key, candidate in changes.items():
                spec = OPTION_SPECS_BY_KEY.get(key)
                if spec is None:
                    code = (
                        "secret_option_forbidden"
                        if is_sensitive_option_key(key)
                        else "unknown_option"
                    )
                    raise OptionsValidationError(
                        "Cette option n’est pas administrable.", code=code, field=key
                    )
                if not spec.editable:
                    raise OptionsValidationError(
                        "Cette option est en lecture seule.", code="readonly_option", field=key
                    )
                normalized = normalize_option_value(spec, candidate)
                if values[key] != normalized:
                    values[key] = normalized
                    changed_keys.append(key)

            validate_cross_options(values)
            if not changed_keys:
                return OptionsUpdate(
                    fields=self._fields(values),
                    changed_keys=(),
                    restart_required=False,
                )

            content = _serialize(values)
            self._atomic_write(content, previous=raw, expected_identity=identity)
            return OptionsUpdate(
                fields=self._fields(values),
                changed_keys=tuple(changed_keys),
                restart_required=any(
                    OPTION_SPECS_BY_KEY[key].restart_required for key in changed_keys
                ),
            )

    @staticmethod
    def _fields(values: Mapping[str, OptionValue]) -> list[OptionFieldValue]:
        return [OptionFieldValue(spec=spec, value=values[spec.key]) for spec in OPTION_SPECS]

    def _values(self, raw: bytes | None) -> dict[str, OptionValue]:
        values = {spec.key: spec.default for spec in OPTION_SPECS}
        if raw is not None:
            values.update(_parse(raw))
        validate_cross_options(values)
        return values

    def _read_optional(self) -> tuple[bytes | None, FileIdentity | None]:
        try:
            with self._control_directory_fd() as directory_fd:
                try:
                    file_fd = os.open(
                        OPTIONS_FILENAME,
                        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                except FileNotFoundError:
                    return None, None
                try:
                    file_stat = os.fstat(file_fd)
                    _validate_options_file(file_stat, max_bytes=self._max_bytes)
                    raw = _read_bounded(file_fd, self._max_bytes)
                    identity = (
                        file_stat.st_dev,
                        file_stat.st_ino,
                        file_stat.st_size,
                        file_stat.st_mtime_ns,
                    )
                    return raw, identity
                finally:
                    os.close(file_fd)
        except FileNotFoundError:
            return None, None
        except PermissionError as exc:
            raise OptionsUnavailableError("Le fichier .options ne peut pas être lu.") from exc
        except NewGreedyConfigUnsafeError as exc:
            raise OptionsUnsafeError("Le chemin de .options est dangereux.") from exc
        except OptionsError:
            raise
        except OSError as exc:
            raise OptionsUnsafeError("Le chemin de .options est dangereux.") from exc

    def _atomic_write(
        self,
        content: bytes,
        *,
        previous: bytes | None,
        expected_identity: FileIdentity | None,
    ) -> None:
        if len(content) > self._max_bytes:
            raise OptionsValidationError("Le fichier .options dépasserait la taille autorisée.")
        try:
            with self._control_directory_fd() as directory_fd:
                _check_identity(directory_fd, expected_identity, max_bytes=self._max_bytes)
                if previous is not None:
                    self._replace_named(directory_fd, BACKUP_FILENAME, previous)
                self._replace_named(directory_fd, OPTIONS_FILENAME, content)
                os.fsync(directory_fd)
        except FileNotFoundError as exc:
            raise OptionsUnavailableError("Le dossier de contrôle .options est absent.") from exc
        except PermissionError as exc:
            raise OptionsUnavailableError("Le fichier .options ne peut pas être écrit.") from exc
        except NewGreedyConfigUnsafeError as exc:
            raise OptionsUnsafeError("Le chemin de .options est dangereux.") from exc
        except OptionsError:
            raise
        except OSError as exc:
            raise OptionsUnsafeError("L’écriture atomique de .options a échoué.") from exc

    @staticmethod
    def _replace_named(directory_fd: int, filename: str, content: bytes) -> None:
        temporary_name = f".{filename}.{secrets.token_hex(12)}.tmp"
        created = False
        try:
            temporary_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
            created = True
            try:
                view = memoryview(content)
                while view:
                    written = os.write(temporary_fd, view)
                    if written <= 0:
                        raise OSError("Short .options write")
                    view = view[written:]
                os.fsync(temporary_fd)
            finally:
                os.close(temporary_fd)
            os.replace(
                temporary_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            created = False
        finally:
            if created:
                with suppress(OSError):
                    os.unlink(temporary_name, dir_fd=directory_fd)

    def _control_directory_fd(self) -> SecureDirectoryChain:
        return SecureDirectoryChain(self._data_root, (CONTROL_DIRECTORY,))


def _validate_options_file(file_stat: os.stat_result, *, max_bytes: int) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise OptionsUnsafeError(".options n’est pas un fichier régulier.")
    if file_stat.st_mode & 0o077:
        raise OptionsUnsafeError("Les permissions de .options sont trop ouvertes.")
    if file_stat.st_uid != os.geteuid():
        raise OptionsUnsafeError("Le propriétaire de .options est invalide.")
    if file_stat.st_size > max_bytes:
        raise OptionsUnsafeError("Le fichier .options est trop volumineux.")


def _read_bounded(file_fd: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining > 0:
        chunk = os.read(file_fd, min(4096, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) > maximum:
        raise OptionsUnsafeError("Le fichier .options est trop volumineux.")
    return raw


def _check_identity(
    directory_fd: int,
    expected: FileIdentity | None,
    *,
    max_bytes: int,
) -> None:
    try:
        current = os.stat(OPTIONS_FILENAME, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if expected is None:
            return
        raise OptionsUnsafeError(".options a disparu pendant la modification.") from None
    if expected is None:
        raise OptionsUnsafeError(".options a été créé simultanément.")
    identity = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
    if identity != expected:
        raise OptionsUnsafeError(".options a été modifié simultanément.")
    _validate_options_file(current, max_bytes=max_bytes)


def _parse(raw: bytes) -> dict[str, OptionValue]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OptionsValidationError("Le fichier .options doit être en UTF-8.") from exc
    if "\x00" in text:
        raise OptionsValidationError("Le fichier .options contient un caractère interdit.")
    lines = text.splitlines()
    if len(lines) > MAX_OPTION_LINES:
        raise OptionsValidationError("Le fichier .options contient trop de lignes.")
    values: dict[str, OptionValue] = {}
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        if "=" not in line:
            raise OptionsValidationError(
                f"La ligne {line_number} de .options est invalide.", code="malformed_options"
            )
        key, raw_value = (part.strip() for part in line.split("=", 1))
        if _KEY_RE.fullmatch(key) is None or key in values:
            raise OptionsValidationError(
                f"La clé de la ligne {line_number} est invalide ou dupliquée.",
                code="malformed_options",
                field=key or None,
            )
        if is_sensitive_option_key(key):
            raise OptionsValidationError(
                "Les secrets sont interdits dans .options.",
                code="secret_option_forbidden",
                field=key,
            )
        spec = OPTION_SPECS_BY_KEY.get(key)
        if spec is None:
            raise OptionsValidationError(
                "Le fichier .options contient une clé inconnue.",
                code="unknown_option",
                field=key,
            )
        values[key] = _parse_value(spec, raw_value)
    return values


def _parse_value(spec: OptionSpec, raw_value: str) -> OptionValue:
    if len(raw_value) > 128 or "\r" in raw_value or "\n" in raw_value:
        raise OptionsValidationError("La valeur de l’option est invalide.", field=spec.key)
    if spec.input_type == "boolean":
        if raw_value == "true":
            return True
        if raw_value == "false":
            return False
        raise OptionsValidationError("Une valeur booléenne est attendue.", field=spec.key)
    if spec.input_type == "integer":
        try:
            value = int(raw_value, 10)
        except ValueError as exc:
            raise OptionsValidationError("Un nombre entier est attendu.", field=spec.key) from exc
        return normalize_option_value(spec, value)
    if raw_value not in spec.choices:
        raise OptionsValidationError("Cette valeur n’est pas autorisée.", field=spec.key)
    return raw_value


def normalize_option_value(spec: OptionSpec, value: OptionValue) -> OptionValue:
    if spec.input_type == "boolean":
        if type(value) is not bool:
            raise OptionsValidationError("Une valeur booléenne est attendue.", field=spec.key)
        return value
    if spec.input_type == "integer":
        if type(value) is not int:
            raise OptionsValidationError("Un nombre entier est attendu.", field=spec.key)
        if spec.minimum is not None and value < spec.minimum:
            raise OptionsValidationError(f"La valeur minimale est {spec.minimum}.", field=spec.key)
        if spec.maximum is not None and value > spec.maximum:
            raise OptionsValidationError(f"La valeur maximale est {spec.maximum}.", field=spec.key)
        return value
    if not isinstance(value, str) or value not in spec.choices:
        raise OptionsValidationError("Cette valeur n’est pas autorisée.", field=spec.key)
    return value


def validate_cross_options(values: Mapping[str, OptionValue]) -> None:
    def integer(key: str) -> int:
        value = values[key]
        if type(value) is not int:
            raise OptionsValidationError("Un nombre entier est attendu.", field=key)
        return value

    warning = integer("WOS_STORAGE_PRESSURE_WARNING_PERCENT")
    critical = integer("WOS_STORAGE_PRESSURE_CRITICAL_PERCENT")
    if warning >= critical:
        raise OptionsValidationError(
            "Le seuil d’alerte doit être inférieur au seuil critique.",
            code="inconsistent_options",
            field="WOS_STORAGE_PRESSURE_WARNING_PERCENT",
        )
    progress_ttl = integer("WOS_CACHE_PROGRESS_TTL_SECONDS")
    default_ttl = integer("WOS_CACHE_DEFAULT_TTL_SECONDS")
    if progress_ttl > default_ttl:
        raise OptionsValidationError(
            "Le TTL des progressions ne peut pas dépasser le TTL par défaut.",
            code="inconsistent_options",
            field="WOS_CACHE_PROGRESS_TTL_SECONDS",
        )
    per_user_speed = integer("WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER")
    global_speed = integer("WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_GLOBAL")
    if global_speed != 0 and per_user_speed > global_speed:
        raise OptionsValidationError(
            "Le débit par utilisateur ne peut pas dépasser le débit global.",
            code="inconsistent_options",
            field="WOS_DOWNLOAD_MAX_BYTES_PER_SECOND_PER_USER",
        )
    managed_quota = integer("WOS_STORAGE_MANAGED_MAX_BYTES")
    user_quota = integer("WOS_STORAGE_USER_MAX_BYTES")
    if managed_quota != 0 and user_quota > managed_quota:
        raise OptionsValidationError(
            "Le quota utilisateur ne peut pas dépasser l’espace total géré.",
            code="inconsistent_options",
            field="WOS_STORAGE_USER_MAX_BYTES",
        )


def _serialize(values: Mapping[str, OptionValue]) -> bytes:
    lines = [
        "# World of Seeds — réglages fonctionnels administrables",
        "# Ne jamais ajouter de mot de passe, token, passkey ou secret.",
    ]
    previous_category = None
    for spec in OPTION_SPECS:
        if previous_category != spec.category:
            lines.extend(("", f"# [{spec.category}]"))
            previous_category = spec.category
        value = values[spec.key]
        serialized = "true" if value is True else "false" if value is False else str(value)
        lines.append(f"{spec.key}={serialized}")
    return ("\n".join(lines) + "\n").encode("utf-8")
