#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

PBKDF2_ITERATIONS = 100_000
PBKDF2_SALT_BYTES = 16
PBKDF2_KEY_BYTES = 64
EXPECTED_QBITTORRENT_URL = "http://qbittorrent:8080"

_STATIC_SETTINGS: dict[tuple[str, str], str] = {
    ("BitTorrent", r"Session\DefaultSavePath"): "/data",
    ("BitTorrent", r"Session\ProxyPeerConnections"): "false",
    ("Meta", "MigrationVersion"): "8",
    ("Network", r"Proxy\AuthEnabled"): "false",
    ("Network", r"Proxy\HostnameLookupEnabled"): "false",
    ("Network", r"Proxy\IP"): "newgreedy",
    ("Network", r"Proxy\Port"): "3456",
    ("Network", r"Proxy\Profiles\BitTorrent"): "true",
    ("Network", r"Proxy\Profiles\Misc"): "false",
    ("Network", r"Proxy\Profiles\RSS"): "false",
    ("Network", r"Proxy\Type"): "HTTP",
    ("Preferences", r"WebUI\Address"): "*",
    ("Preferences", r"WebUI\CSRFProtection"): "true",
    ("Preferences", r"WebUI\HostHeaderValidation"): "true",
    ("Preferences", r"WebUI\LocalHostAuth"): "false",
    ("Preferences", r"WebUI\Port"): "8080",
    ("Preferences", r"WebUI\ServerDomains"): "qbittorrent",
}


class BootstrapError(RuntimeError):
    pass


def _compose_registry(compose_config: dict[str, Any]) -> str:
    services = compose_config.get("services")
    if not isinstance(services, dict):
        raise BootstrapError("normalized Compose services are missing")
    scheduler = services.get("scheduler")
    if not isinstance(scheduler, dict):
        raise BootstrapError("scheduler service is missing")
    environment = scheduler.get("environment")
    if not isinstance(environment, dict):
        raise BootstrapError("scheduler environment is missing")
    registry = environment.get("WOS_INTEGRATION_ACCOUNTS_JSON")
    if not isinstance(registry, str) or not registry:
        raise BootstrapError("scheduler integration registry is missing")
    return registry


def _qbittorrent_credentials(compose_config: dict[str, Any]) -> tuple[str, str]:
    try:
        registry = json.loads(_compose_registry(compose_config))
    except json.JSONDecodeError as exc:
        raise BootstrapError("scheduler integration registry is invalid JSON") from exc

    if not isinstance(registry, dict):
        raise BootstrapError("scheduler integration registry must be an object")
    routes = registry.get("routes")
    if not isinstance(routes, list) or not routes:
        raise BootstrapError("at least one Rise2 integration route is required")

    credentials: set[tuple[str, str]] = set()
    for route in routes:
        if not isinstance(route, dict):
            raise BootstrapError("Rise2 integration route is invalid")
        url = route.get("qbittorrent_url")
        username = route.get("qbittorrent_username")
        password = route.get("qbittorrent_password")
        if url != EXPECTED_QBITTORRENT_URL:
            raise BootstrapError("all Rise2 routes must use the internal qBittorrent service")
        if not isinstance(username, str) or not username or ":" in username:
            raise BootstrapError("qBittorrent username is invalid")
        if not isinstance(password, str) or len(password) < 6:
            raise BootstrapError("qBittorrent password is invalid")
        credentials.add((username, password))

    if len(credentials) != 1:
        raise BootstrapError("all Rise2 routes must share one qBittorrent WebUI credential")
    return next(iter(credentials))


def _password_hash(password: str, *, salt: bytes | None = None) -> str:
    actual_salt = secrets.token_bytes(PBKDF2_SALT_BYTES) if salt is None else salt
    if len(actual_salt) != PBKDF2_SALT_BYTES:
        raise BootstrapError("qBittorrent PBKDF2 salt has an invalid length")
    key = hashlib.pbkdf2_hmac(
        "sha512",
        password.encode("utf-8"),
        actual_salt,
        PBKDF2_ITERATIONS,
        dklen=PBKDF2_KEY_BYTES,
    )
    return (
        "@ByteArray("
        + base64.b64encode(actual_salt).decode("ascii")
        + ":"
        + base64.b64encode(key).decode("ascii")
        + ")"
    )


def _render(username: str, password: str, *, salt: bytes | None = None) -> str:
    settings = dict(_STATIC_SETTINGS)
    settings[("Preferences", r"WebUI\Username")] = username
    settings[("Preferences", r"WebUI\Password_PBKDF2")] = (
        f'"{_password_hash(password, salt=salt)}"'
    )

    sections: dict[str, list[tuple[str, str]]] = {}
    for (section, key), value in settings.items():
        sections.setdefault(section, []).append((key, value))

    lines: list[str] = []
    for section in sorted(sections):
        if lines:
            lines.append("")
        lines.append(f"[{section}]")
        for key, value in sorted(sections[section]):
            lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _parse_ini(text: str) -> dict[tuple[str, str], str]:
    values: dict[tuple[str, str], str] = {}
    section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[(section, key.strip())] = value.strip()
    return values


def _verify_password(stored: str, password: str) -> bool:
    candidate = stored.strip().strip('"')
    if not (candidate.startswith("@ByteArray(") and candidate.endswith(")")):
        return False
    payload = candidate[len("@ByteArray(") : -1]
    try:
        salt64, key64 = payload.split(":", 1)
        salt = base64.b64decode(salt64, validate=True)
        stored_key = base64.b64decode(key64, validate=True)
    except (ValueError, binascii.Error):
        return False
    if len(salt) != PBKDF2_SALT_BYTES or len(stored_key) != PBKDF2_KEY_BYTES:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha512",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=len(stored_key),
    )
    return hmac.compare_digest(actual, stored_key)


def _matches(text: str, username: str, password: str) -> bool:
    values = _parse_ini(text)
    for key, expected in _STATIC_SETTINGS.items():
        if values.get(key) != expected:
            return False
    if values.get(("Preferences", r"WebUI\Username")) != username:
        return False
    stored = values.get(("Preferences", r"WebUI\Password_PBKDF2"))
    return stored is not None and _verify_password(stored, password)


def _set_metadata(path: Path, uid: int, gid: int) -> None:
    os.chmod(path, 0o600)
    metadata = path.stat()
    if metadata.st_uid != uid or metadata.st_gid != gid:
        os.chown(path, uid, gid)


def ensure_bootstrap(
    compose_config: dict[str, Any],
    output: Path,
    *,
    uid: int,
    gid: int,
    salt: bytes | None = None,
) -> bool:
    username, password = _qbittorrent_credentials(compose_config)

    if output.exists():
        if output.is_symlink() or not output.is_file():
            raise BootstrapError("qBittorrent bootstrap path must be a regular file")
        current = output.read_text(encoding="utf-8")
        if _matches(current, username, password):
            _set_metadata(output, uid, gid)
            return False
    elif output.is_symlink():
        raise BootstrapError("qBittorrent bootstrap path must not be a symlink")

    parent = output.parent
    if not parent.is_dir() or parent.is_symlink():
        raise BootstrapError("qBittorrent bootstrap parent directory is invalid")

    rendered = _render(username, password, salt=salt)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=parent, text=True)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        metadata = os.fstat(fd)
        if metadata.st_uid != uid or metadata.st_gid != gid:
            os.fchown(fd, uid, gid)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if fd >= 0:
            os.close(fd)
        temporary.unlink(missing_ok=True)

    _set_metadata(output, uid, gid)
    if stat.S_IMODE(output.stat().st_mode) != 0o600:
        raise BootstrapError("qBittorrent bootstrap mode is invalid after write")
    return True


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render the secret-bearing Rise2 qBittorrent bootstrap"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uid", type=_positive_int, required=True)
    parser.add_argument("--gid", type=_positive_int, required=True)
    args = parser.parse_args()

    try:
        compose_config = json.load(sys.stdin)
        if not isinstance(compose_config, dict):
            raise BootstrapError("normalized Compose config must be an object")
        changed = ensure_bootstrap(
            compose_config,
            args.output,
            uid=args.uid,
            gid=args.gid,
        )
    except (BootstrapError, json.JSONDecodeError, OSError) as exc:
        parser.error(str(exc))

    print(
        "Rise2 qBittorrent bootstrap rendered."
        if changed
        else "Rise2 qBittorrent bootstrap verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
