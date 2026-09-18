import hashlib
from pathlib import Path

import pytest

from app.torrents import ParsedTorrent, TorrentValidationError, sanitize_torrent

FIXTURES = Path(__file__).with_name("fixtures")
REGRESSION_INFO_HASH = "a929d5b7af16a8a585fba8c7c8bddc70d61f1686"


def _bencode(value: object) -> bytes:
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(_bencode(key) + _bencode(value[key]) for key in sorted(value)) + b"e"
    raise TypeError(value)


def _torrent(
    paths: list[list[bytes]],
    *,
    attributes: list[bytes | None] | None = None,
) -> bytes:
    attributes = attributes or [None] * len(paths)
    files = []
    for index, (path, attribute) in enumerate(zip(paths, attributes, strict=True)):
        entry: dict[bytes, object] = {b"length": index + 1, b"path": path}
        if attribute is not None:
            entry[b"attr"] = attribute
        if attribute == b"l":
            entry[b"symlink path"] = [b"outside"]
        files.append(entry)
    return _bencode(
        {
            b"announce": b"https://c411.org/announce/redacted",
            b"info": {
                b"files": files,
                b"name": b"Media",
                b"piece length": 16_384,
                b"pieces": b"p" * 20,
            },
        }
    )


def _sanitize(content: bytes) -> ParsedTorrent:
    return sanitize_torrent(
        content,
        allowed_tracker_hosts=["c411.org", "tk.c411.tw"],
        max_total_size=10**15,
    )


@pytest.mark.parametrize(
    "paths",
    [
        [[b"Film.mkv"]],
        [[b"Film.mkv"], [b"Film.srt"], [b"Film.nfo"]],
        [["Été à Tokyo.mkv".encode()], ["Sous-titre français.srt".encode()]],
    ],
)
def test_media_allowlist_accepts_normal_content(paths: list[list[bytes]]) -> None:
    parsed = _sanitize(_torrent(paths))

    assert len(parsed.files) == len(paths)


@pytest.mark.parametrize("extension", ["sh", "py", "exe", "desktop"])
def test_media_allowlist_rejects_active_file_types(extension: str) -> None:
    with pytest.raises(TorrentValidationError) as failure:
        _sanitize(_torrent([[f"payload.{extension}".encode()]]))

    assert failure.value.code == "torrent_file_type_not_allowed"
    assert "non autorisé" in str(failure.value)


@pytest.mark.parametrize(
    "path",
    [
        [b"..", b"escape.mkv"],
        [b"/etc", b"escape.mkv"],
        [b"C:", b"escape.mkv"],
        [b"folder\\..\\escape.mkv"],
        [b"folder", b"bad\x00name.mkv"],
    ],
)
def test_torrent_rejects_traversal_absolute_and_ambiguous_paths(path: list[bytes]) -> None:
    with pytest.raises(TorrentValidationError):
        _sanitize(_torrent([path]))


def test_torrent_rejects_symlink_and_executable_attributes() -> None:
    for attribute in (b"l", b"x"):
        with pytest.raises(TorrentValidationError) as failure:
            _sanitize(_torrent([[b"Film.mkv"]], attributes=[attribute]))
        assert failure.value.code == "torrent_unsafe_file_attribute"


@pytest.mark.parametrize(
    "paths",
    [
        [[b"Film.mkv"], [b"Film.mkv"]],
        [[b"Film.mkv"], [b"film.MKV"]],
        [[b"Film.mkv"], [b"Film.mkv", b"subtitle.srt"]],
    ],
)
def test_torrent_rejects_exact_normalized_and_prefix_path_collisions(
    paths: list[list[bytes]],
) -> None:
    with pytest.raises(TorrentValidationError):
        _sanitize(_torrent(paths))


def test_recent_real_torrent_regression_remains_valid_and_secret_free() -> None:
    content = (FIXTURES / f"{REGRESSION_INFO_HASH}.torrent").read_bytes()
    parsed = _sanitize(content)

    assert parsed.info_hash == REGRESSION_INFO_HASH
    assert hashlib.sha1(content, usedforsecurity=False).hexdigest() != REGRESSION_INFO_HASH
    assert len(parsed.files) == 8
    assert all(item.relative_path.casefold().endswith(".mkv") for item in parsed.files)
    assert b"private-user-passkey" not in content
