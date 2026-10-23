import hashlib

import pytest

from app.torrents import TorrentValidationError, normalize_torrent


def bencode(value: object) -> bytes:
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        return b"d" + b"".join(bencode(key) + bencode(value[key]) for key in sorted(value)) + b"e"
    raise TypeError(value)


def torrent_content(
    *,
    name: bytes = b"Film.mkv",
    tracker: bytes = b"https://c411.org/anything/old-user-passkey",
    announce_list: list[list[bytes]] | None = None,
) -> bytes:
    info = {
        b"length": 5,
        b"name": name,
        b"piece length": 16_384,
        b"pieces": b"p" * 20,
    }
    metainfo: dict[bytes, object] = {b"announce": tracker, b"info": info}
    if announce_list is not None:
        metainfo[b"announce-list"] = announce_list
    return bencode(metainfo)


def test_torrent_normalization_replaces_passkeys_and_preserves_info_hash() -> None:
    source = torrent_content(
        announce_list=[
            [b"https://c411.org/anything/old-user-passkey"],
            [b"https://tk.c411.tw/anything/old-user-passkey"],
        ]
    )
    info_raw = bencode(
        {
            b"length": 5,
            b"name": b"Film.mkv",
            b"piece length": 16_384,
            b"pieces": b"p" * 20,
        }
    )

    result = normalize_torrent(
        source,
        passkey="test-passkey-123",
        allowed_tracker_hosts=["c411.org", "tk.c411.tw"],
        max_total_size=1_000,
    )

    assert b"old-user-passkey" not in result.content
    assert result.content.count(b"https://c411.org/announce/test-passkey-123") == 2
    assert b"https://tk.c411.tw/announce/test-passkey-123" in result.content
    assert result.info_hash == hashlib.sha1(info_raw, usedforsecurity=False).hexdigest()
    assert result.name == "Film.mkv"
    assert result.total_size == 5


@pytest.mark.parametrize(
    "content",
    [
        b"not-bencode",
        torrent_content(tracker=b"https://evil.example/anything/old-user-passkey"),
        torrent_content(name=b"../Film.mkv"),
    ],
)
def test_torrent_normalization_rejects_invalid_or_unauthorized_files(content: bytes) -> None:
    with pytest.raises(TorrentValidationError):
        normalize_torrent(
            content,
            passkey="test-passkey-123",
            allowed_tracker_hosts=["c411.org", "tk.c411.tw"],
            max_total_size=1_000,
        )


def test_peer_discovery_fields_outside_info_are_dropped_without_changing_the_info_hash() -> None:
    info = {
        b"length": 5,
        b"name": b"Film.mkv",
        b"piece length": 16_384,
        b"pieces": b"p" * 20,
    }
    plain = bencode({b"announce": b"https://c411.org/anything/old-user-passkey", b"info": info})
    enriched = bencode(
        {
            b"announce": b"https://c411.org/anything/old-user-passkey",
            b"url-list": [b"http://qbittorrent:8080/api/v2/app/version"],
            b"httpseeds": [b"http://api:8000/seed"],
            b"nodes": [[b"203.0.113.9", 6881]],
            b"comment": b"visit http://example.invalid",
            b"created by": b"attacker",
            b"info": info,
        }
    )

    kwargs = {
        "passkey": "test-passkey-123",
        "allowed_tracker_hosts": ["c411.org"],
        "max_total_size": 1_000,
    }
    cleaned = normalize_torrent(enriched, **kwargs)  # type: ignore[arg-type]
    reference = normalize_torrent(plain, **kwargs)  # type: ignore[arg-type]

    for key in (b"url-list", b"httpseeds", b"nodes", b"comment", b"created by"):
        assert key not in cleaned.content
    assert b"qbittorrent:8080" not in cleaned.content
    assert cleaned.info_hash == reference.info_hash
    assert cleaned.content == reference.content
