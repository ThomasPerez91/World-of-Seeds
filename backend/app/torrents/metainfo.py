from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

MAX_BENCODE_DEPTH = 64
MAX_BENCODE_ITEMS = 200_000

BValue = int | bytes | list["BValue"] | dict[bytes, "BValue"]


class TorrentValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedTorrent:
    content: bytes
    info_hash: str
    name: str
    total_size: int


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


def _tracker_url(raw: bytes, *, passkey: str, allowed_hosts: frozenset[str]) -> bytes:
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
    path = f"/{quote(passkey, safe='')}/announce"
    return urlunsplit((parsed.scheme, netloc, path, "", "")).encode("ascii")


def _normalize_trackers(
    metainfo: dict[bytes, BValue], *, passkey: str, allowed_hosts: frozenset[str]
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


def _torrent_name(info: dict[bytes, BValue]) -> str:
    raw_name = info.get(b"name.utf-8", info.get(b"name"))
    if not isinstance(raw_name, bytes):
        raise TorrentValidationError("Le torrent ne contient pas de nom valide.")
    try:
        name = raw_name.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TorrentValidationError("Le nom du torrent n’est pas en UTF-8.") from exc
    if (
        not name
        or len(name) > 4096
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise TorrentValidationError("Le nom du torrent est invalide.")
    return name


def _torrent_size(info: dict[bytes, BValue]) -> int:
    single_length = info.get(b"length")
    files = info.get(b"files")
    if isinstance(single_length, int) and files is None:
        total = single_length
    elif isinstance(files, list) and single_length is None and files:
        total = 0
        for raw_file in files:
            file_entry = _required_dictionary(raw_file, "files")
            length = file_entry.get(b"length")
            path = file_entry.get(b"path.utf-8", file_entry.get(b"path"))
            if not isinstance(length, int) or length < 0 or not isinstance(path, list) or not path:
                raise TorrentValidationError("La liste des fichiers du torrent est invalide.")
            if not all(
                isinstance(component, bytes)
                and component not in {b"", b".", b".."}
                and b"/" not in component
                and b"\\" not in component
                and b"\x00" not in component
                for component in path
            ):
                raise TorrentValidationError("Un chemin du torrent est invalide.")
            total += length
    else:
        raise TorrentValidationError("La taille du torrent est invalide.")
    if total < 0:
        raise TorrentValidationError("La taille du torrent est invalide.")
    return total


def normalize_torrent(
    content: bytes,
    *,
    passkey: str,
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
    total_size = _torrent_size(info)
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
    )
