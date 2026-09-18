from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, urlsplit, urlunsplit

MAX_BENCODE_DEPTH = 64
MAX_BENCODE_ITEMS = 200_000
MAX_TORRENT_FILES = 100_000
MAX_PATH_COMPONENT_BYTES = 255
MAX_TORRENT_PATH_BYTES = 4096

# WOS is a media delivery service. Keeping this list explicit prevents a new
# executable/script format from becoming downloadable merely because it was
# not present in a denylist. Additions require a reviewed code change.
ALLOWED_CONTENT_EXTENSIONS = frozenset(
    {
        # Video and optical-disc metadata.
        "3g2",
        "3gp",
        "avi",
        "bdmv",
        "bup",
        "clpi",
        "divx",
        "flv",
        "ifo",
        "m2ts",
        "m4v",
        "mkv",
        "mov",
        "mp4",
        "mpeg",
        "mpg",
        "mpls",
        "mts",
        "ogv",
        "ts",
        "vob",
        "webm",
        "wmv",
        # Audio.
        "aac",
        "ac3",
        "aif",
        "aiff",
        "alac",
        "ape",
        "dts",
        "flac",
        "m4a",
        "mka",
        "mp3",
        "oga",
        "ogg",
        "opus",
        "wav",
        "wma",
        # Subtitles.
        "ass",
        "dfxp",
        "idx",
        "smi",
        "smil",
        "srt",
        "ssa",
        "sub",
        "sup",
        "ttml",
        "vtt",
        # Images and benign release metadata.
        "avif",
        "bmp",
        "cue",
        "gif",
        "heic",
        "heif",
        "jpeg",
        "jpg",
        "json",
        "md5",
        "nfo",
        "png",
        "sfv",
        "sha1",
        "sha256",
        "tif",
        "tiff",
        "txt",
        "webp",
        "xml",
    }
)
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:$")

BValue = int | bytes | list["BValue"] | dict[bytes, "BValue"]


class TorrentValidationError(ValueError):
    def __init__(self, message: str, *, code: str = "torrent_invalid") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParsedTorrent:
    content: bytes
    info_hash: str
    name: str
    total_size: int
    files: tuple[TorrentContentFile, ...]


@dataclass(frozen=True, slots=True)
class TorrentContentFile:
    file_index: int
    relative_path: str
    size: int


@dataclass(frozen=True, slots=True)
class _RawValue:
    content: bytes


class _Parser:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.position = 0
        self.items = 0

    def parse(self, depth: int = 0) -> BValue:
        if depth > MAX_BENCODE_DEPTH or self.position >= len(self.content):
            raise TorrentValidationError("Le fichier torrent est invalide.")
        self.items += 1
        if self.items > MAX_BENCODE_ITEMS:
            raise TorrentValidationError("Le fichier torrent contient trop d’éléments.")

        marker = self.content[self.position]
        if marker == ord("i"):
            return self._integer()
        if marker == ord("l"):
            return self._list(depth)
        if marker == ord("d"):
            return self._dictionary(depth)
        if ord("0") <= marker <= ord("9"):
            return self._bytes()
        raise TorrentValidationError("Le fichier torrent est invalide.")

    def _integer(self) -> int:
        end = self.content.find(b"e", self.position + 1)
        if end < 0:
            raise TorrentValidationError("Le fichier torrent est invalide.")
        raw = self.content[self.position + 1 : end]
        digits = raw[1:] if raw.startswith(b"-") else raw
        if (
            not raw
            or raw == b"-0"
            or (raw.startswith(b"0") and len(raw) > 1)
            or raw.startswith(b"-0")
            or not digits
            or not digits.isdigit()
        ):
            raise TorrentValidationError("Le fichier torrent est invalide.")
        self.position = end + 1
        return int(raw)

    def _bytes(self) -> bytes:
        colon = self.content.find(b":", self.position)
        if colon < 0:
            raise TorrentValidationError("Le fichier torrent est invalide.")
        raw_length = self.content[self.position : colon]
        if (
            not raw_length
            or not raw_length.isdigit()
            or (raw_length.startswith(b"0") and len(raw_length) > 1)
        ):
            raise TorrentValidationError("Le fichier torrent est invalide.")
        length = int(raw_length)
        start = colon + 1
        end = start + length
        if end > len(self.content):
            raise TorrentValidationError("Le fichier torrent est tronqué.")
        self.position = end
        return self.content[start:end]

    def _list(self, depth: int) -> list[BValue]:
        self.position += 1
        result: list[BValue] = []
        while self.position < len(self.content) and self.content[self.position] != ord("e"):
            result.append(self.parse(depth + 1))
        if self.position >= len(self.content):
            raise TorrentValidationError("Le fichier torrent est tronqué.")
        self.position += 1
        return result

    def _dictionary(self, depth: int) -> dict[bytes, BValue]:
        self.position += 1
        result: dict[bytes, BValue] = {}
        previous: bytes | None = None
        while self.position < len(self.content) and self.content[self.position] != ord("e"):
            key = self._bytes()
            if key in result or (previous is not None and key < previous):
                raise TorrentValidationError("Le dictionnaire torrent n’est pas canonique.")
            previous = key
            result[key] = self.parse(depth + 1)
        if self.position >= len(self.content):
            raise TorrentValidationError("Le fichier torrent est tronqué.")
        self.position += 1
        return result


def _encode(value: BValue | _RawValue) -> bytes:
    if isinstance(value, _RawValue):
        return value.content
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b"e"
    if isinstance(value, list):
        return b"l" + b"".join(_encode(item) for item in value) + b"e"
    return b"d" + b"".join(_encode(key) + _encode(value[key]) for key in sorted(value)) + b"e"


def _tracker_url(raw: bytes, *, passkey: str | None, allowed_hosts: frozenset[str]) -> bytes:
    try:
        value = raw.decode("ascii")
        parsed = urlsplit(value)
        port = parsed.port
    except (UnicodeDecodeError, ValueError) as exc:
        raise TorrentValidationError("L’URL du tracker C411 est invalide.") from exc
    hostname = parsed.hostname.lower() if parsed.hostname is not None else None
    if (
        parsed.scheme not in {"http", "https"}
        or hostname not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        raise TorrentValidationError("Ce torrent n’utilise pas un tracker C411 autorisé.")
    netloc = hostname if port is None else f"{hostname}:{port}"
    path = "/announce" if passkey is None else f"/announce/{quote(passkey, safe='')}"
    return urlunsplit((parsed.scheme, netloc, path, "", "")).encode("ascii")


def _normalize_trackers(
    metainfo: dict[bytes, BValue], *, passkey: str | None, allowed_hosts: frozenset[str]
) -> None:
    announce = metainfo.get(b"announce")
    if not isinstance(announce, bytes):
        raise TorrentValidationError("Le torrent ne contient aucun tracker principal.")
    metainfo[b"announce"] = _tracker_url(
        announce,
        passkey=passkey,
        allowed_hosts=allowed_hosts,
    )

    announce_list = metainfo.get(b"announce-list")
    if announce_list is None:
        return
    if not isinstance(announce_list, list) or not announce_list:
        raise TorrentValidationError("La liste de trackers est invalide.")
    normalized_tiers: list[BValue] = []
    for tier in announce_list:
        if not isinstance(tier, list) or not tier:
            raise TorrentValidationError("La liste de trackers est invalide.")
        normalized_tier: list[BValue] = []
        for tracker in tier:
            if not isinstance(tracker, bytes):
                raise TorrentValidationError("La liste de trackers est invalide.")
            normalized_tier.append(
                _tracker_url(tracker, passkey=passkey, allowed_hosts=allowed_hosts)
            )
        normalized_tiers.append(normalized_tier)
    metainfo[b"announce-list"] = normalized_tiers


def _required_dictionary(value: BValue | None, field: str) -> dict[bytes, BValue]:
    if not isinstance(value, dict):
        raise TorrentValidationError(f"Le champ torrent {field} est invalide.")
    return value


def _decode_utf8(raw: bytes, *, error_message: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TorrentValidationError(error_message) from exc


def _select_utf8_bytes(
    value: dict[bytes, BValue],
    field: bytes,
    *,
    error_message: str,
) -> bytes:
    raw = value.get(field)
    raw_utf8 = value.get(field + b".utf-8")
    if raw_utf8 is not None:
        if not isinstance(raw_utf8, bytes):
            raise TorrentValidationError(error_message)
        if raw is not None:
            if not isinstance(raw, bytes):
                raise TorrentValidationError(error_message)
            decoded = _decode_utf8(raw, error_message=error_message)
            decoded_utf8 = _decode_utf8(raw_utf8, error_message=error_message)
            if unicodedata.normalize("NFC", decoded) != unicodedata.normalize("NFC", decoded_utf8):
                raise TorrentValidationError(error_message)
        return raw_utf8
    if not isinstance(raw, bytes):
        raise TorrentValidationError(error_message)
    return raw


def _validate_attributes(entry: dict[bytes, BValue], *, field: str) -> frozenset[str]:
    raw_attributes = entry.get(b"attr")
    attributes: frozenset[str]
    if raw_attributes is None:
        attributes = frozenset()
    elif isinstance(raw_attributes, bytes):
        try:
            attributes = frozenset(raw_attributes.decode("ascii"))
        except UnicodeDecodeError as exc:
            raise TorrentValidationError(f"Les attributs torrent {field} sont invalides.") from exc
    else:
        raise TorrentValidationError(f"Les attributs torrent {field} sont invalides.")

    # BEP/libtorrent use `l` for symlinks and `x` for executable files. Only
    # padding and hidden markers are harmless for WOS; fail closed on future
    # or unknown file types instead of letting qBittorrent interpret them.
    if b"symlink path" in entry or not attributes.issubset({"p", "h"}):
        raise TorrentValidationError(
            "Le torrent contient un lien symbolique ou un attribut de fichier dangereux.",
            code="torrent_unsafe_file_attribute",
        )
    return attributes


def _validate_component(component: str, *, first: bool = False) -> None:
    encoded = component.encode("utf-8")
    if (
        not component
        or component in {".", ".."}
        or "/" in component
        or "\\" in component
        or "\x00" in component
        or len(encoded) > MAX_PATH_COMPONENT_BYTES
        or component.endswith((" ", "."))
        or any(unicodedata.category(character) == "Cc" for character in component)
        or (first and _WINDOWS_DRIVE_RE.fullmatch(component) is not None)
    ):
        raise TorrentValidationError("Un chemin du torrent est invalide.")


def _path_collision_key(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in parts)


def _require_allowed_file_type(filename: str, *, padding: bool = False) -> None:
    if padding:
        return
    _, separator, extension = filename.rpartition(".")
    if not separator or extension.casefold() not in ALLOWED_CONTENT_EXTENSIONS:
        raise TorrentValidationError(
            "Le torrent contient un type de fichier non autorisé.",
            code="torrent_file_type_not_allowed",
        )


def _torrent_name(info: dict[bytes, BValue]) -> str:
    raw_name = _select_utf8_bytes(
        info,
        b"name",
        error_message="Le torrent ne contient pas de nom UTF-8 valide.",
    )
    name = _decode_utf8(raw_name, error_message="Le nom du torrent n’est pas en UTF-8.")
    try:
        _validate_component(name, first=True)
    except TorrentValidationError as exc:
        raise TorrentValidationError("Le nom du torrent est invalide.") from exc
    return name


def _torrent_files(
    info: dict[bytes, BValue],
    *,
    torrent_name: str,
) -> tuple[TorrentContentFile, ...]:
    _validate_attributes(info, field="info")
    single_length = info.get(b"length")
    files = info.get(b"files")
    if isinstance(single_length, int) and single_length >= 0 and files is None:
        _require_allowed_file_type(torrent_name)
        return (TorrentContentFile(0, torrent_name, single_length),)
    elif isinstance(files, list) and single_length is None and files:
        if len(files) > MAX_TORRENT_FILES:
            raise TorrentValidationError("Le torrent contient trop de fichiers.")
        entries: list[TorrentContentFile] = []
        collision_paths: set[tuple[str, ...]] = set()
        for file_index, raw_file in enumerate(files):
            file_entry = _required_dictionary(raw_file, "files")
            length = file_entry.get(b"length")
            attributes = _validate_attributes(file_entry, field="files")
            path = file_entry.get(b"path")
            path_utf8 = file_entry.get(b"path.utf-8")
            if (
                not isinstance(length, int)
                or length < 0
                or not isinstance(path, list)
                or not path
                or (path_utf8 is not None and not isinstance(path_utf8, list))
                or path_utf8 == []
            ):
                raise TorrentValidationError("La liste des fichiers du torrent est invalide.")
            selected_path = path_utf8 if isinstance(path_utf8, list) else path
            components: list[str] = []
            for component_index, component in enumerate(selected_path):
                if not isinstance(component, bytes):
                    raise TorrentValidationError("Un chemin du torrent est invalide.")
                decoded = _decode_utf8(
                    component,
                    error_message="Un chemin du torrent n’est pas en UTF-8.",
                )
                _validate_component(decoded, first=component_index == 0)
                components.append(decoded)
            if isinstance(path_utf8, list):
                if len(path) != len(path_utf8):
                    raise TorrentValidationError("Un chemin du torrent est invalide.")
                fallback_components: list[str] = []
                for component in path:
                    if not isinstance(component, bytes):
                        raise TorrentValidationError("Un chemin du torrent est invalide.")
                    fallback_components.append(
                        _decode_utf8(
                            component,
                            error_message="Un chemin du torrent n’est pas en UTF-8.",
                        )
                    )
                if tuple(
                    unicodedata.normalize("NFC", value) for value in fallback_components
                ) != tuple(unicodedata.normalize("NFC", value) for value in components):
                    raise TorrentValidationError("Un chemin du torrent est invalide.")

            full_parts = (torrent_name, *components)
            relative_path = PurePosixPath(*full_parts).as_posix()
            collision_key = _path_collision_key(full_parts)
            if (
                relative_path.startswith("/")
                or len(relative_path.encode("utf-8")) > MAX_TORRENT_PATH_BYTES
                or collision_key in collision_paths
                or any(
                    existing == collision_key[: len(existing)]
                    or collision_key == existing[: len(collision_key)]
                    for existing in collision_paths
                )
            ):
                raise TorrentValidationError("Un chemin du torrent est invalide.")
            padding = "p" in attributes
            if padding and (components[0] != ".pad" or attributes != {"p"}):
                raise TorrentValidationError("Un fichier de remplissage torrent est invalide.")
            _require_allowed_file_type(components[-1], padding=padding)
            collision_paths.add(collision_key)
            entries.append(TorrentContentFile(file_index, relative_path, length))
        return tuple(entries)
    else:
        raise TorrentValidationError("La taille du torrent est invalide.")


def _rewrite_torrent(
    content: bytes,
    *,
    passkey: str | None,
    allowed_tracker_hosts: list[str],
    max_total_size: int,
) -> ParsedTorrent:
    if not content:
        raise TorrentValidationError("Le fichier torrent est vide.")
    parser = _Parser(content)
    metainfo_value = parser.parse()
    if parser.position != len(content):
        raise TorrentValidationError("Le fichier torrent contient des données superflues.")
    metainfo = _required_dictionary(metainfo_value, "racine")
    info = _required_dictionary(metainfo.get(b"info"), "info")
    if any(field in info for field in (b"file tree", b"meta version", b"pieces root")):
        # WOS currently computes and owns v1 SHA-1 identities. Accepting BEP 52
        # here without traversing its separate file tree would let qBittorrent
        # materialize paths that were never validated by WOS.
        raise TorrentValidationError(
            "Cette structure de torrent n’est pas prise en charge de manière sécurisée."
        )

    info_parser = _Parser(content)
    if content[:1] != b"d":
        raise TorrentValidationError("Le fichier torrent est invalide.")
    info_parser.position = 1
    info_raw: bytes | None = None
    while info_parser.position < len(content) and content[info_parser.position] != ord("e"):
        key = info_parser._bytes()
        start = info_parser.position
        info_parser.parse(1)
        if key == b"info":
            info_raw = content[start : info_parser.position]
    if info_raw is None:
        raise TorrentValidationError("Le torrent ne contient pas de dictionnaire info.")

    pieces = info.get(b"pieces")
    piece_length = info.get(b"piece length")
    if not isinstance(pieces, bytes) or not pieces or len(pieces) % 20 != 0:
        raise TorrentValidationError("Les empreintes de pièces du torrent sont invalides.")
    if not isinstance(piece_length, int) or piece_length <= 0:
        raise TorrentValidationError("La taille des pièces du torrent est invalide.")

    name = _torrent_name(info)
    files = _torrent_files(info, torrent_name=name)
    total_size = sum(file.size for file in files)
    if total_size > max_total_size:
        raise TorrentValidationError("Le contenu demandé dépasse la taille autorisée.")

    _normalize_trackers(
        metainfo,
        passkey=passkey,
        allowed_hosts=frozenset(host.lower() for host in allowed_tracker_hosts),
    )
    encoded_parts: list[bytes] = []
    for key in sorted(metainfo):
        value: BValue | _RawValue = _RawValue(info_raw) if key == b"info" else metainfo[key]
        encoded_parts.extend((_encode(key), _encode(value)))
    normalized = b"d" + b"".join(encoded_parts) + b"e"
    return ParsedTorrent(
        content=normalized,
        info_hash=hashlib.sha1(info_raw, usedforsecurity=False).hexdigest(),
        name=name,
        total_size=total_size,
        files=files,
    )


def normalize_torrent(
    content: bytes,
    *,
    passkey: str,
    allowed_tracker_hosts: list[str],
    max_total_size: int,
) -> ParsedTorrent:
    """Validate metainfo and inject the infrastructure passkey for immediate submission."""

    return _rewrite_torrent(
        content,
        passkey=passkey,
        allowed_tracker_hosts=allowed_tracker_hosts,
        max_total_size=max_total_size,
    )


def sanitize_torrent(
    content: bytes,
    *,
    allowed_tracker_hosts: list[str],
    max_total_size: int,
) -> ParsedTorrent:
    """Validate metainfo and remove tracker credentials before durable staging."""

    return _rewrite_torrent(
        content,
        passkey=None,
        allowed_tracker_hosts=allowed_tracker_hosts,
        max_total_size=max_total_size,
    )
