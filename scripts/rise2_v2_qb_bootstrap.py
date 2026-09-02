#!/usr/bin/env python3
"""Derive the private qB bootstrap from the existing Rise2 secret registry.

qBittorrent release-5.2.3 (0b63c3d17373f6132ea211c9dcd4241284ccdfaf):
src/base/utils/password.cpp, preferences.cpp, net/proxyconfigurationmanager.cpp
and bittorrent/sessionimpl.cpp. No production credential is an argument/output.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

POLICY = Path(__file__).resolve().parents[1] / "deploy/qbittorrent.rise2.conf"
REQUIRED = {
    ("Preferences", r"WebUI\HostHeaderValidation"): "true",
    ("Preferences", r"WebUI\ServerDomains"): "qbittorrent;localhost",
    ("Preferences", r"WebUI\CSRFProtection"): "true",
    ("Preferences", r"WebUI\LocalHostAuth"): "true",
    ("Preferences", r"WebUI\AuthSubnetWhitelistEnabled"): "false",
    ("Preferences", r"WebUI\ReverseProxySupportEnabled"): "false",
    ("Preferences", r"WebUI\Enabled"): "true",
    ("Preferences", r"WebUI\Address"): "*",
    ("Preferences", r"WebUI\Port"): "8080",
    ("Preferences", r"WebUI\HTTPS\Enabled"): "false",
    ("Network", r"Proxy\Type"): "HTTP",
    ("Network", r"Proxy\IP"): "newgreedy",
    ("Network", r"Proxy\Port"): "3456",
    ("Network", r"Proxy\AuthEnabled"): "false",
    ("Network", r"Proxy\HostnameLookupEnabled"): "true",
    ("Network", r"Proxy\Profiles\BitTorrent"): "true",
    ("Network", r"Proxy\Profiles\RSS"): "false",
    ("Network", r"Proxy\Profiles\Misc"): "false",
    ("BitTorrent", r"Session\ProxyPeerConnections"): "false",
    ("BitTorrent", r"Session\DefaultSavePath"): "/data",
}
USERNAME = ("Preferences", r"WebUI\Username")
PASSWORD = ("Preferences", r"WebUI\Password_PBKDF2")


class BootstrapError(RuntimeError):
    """Only fixed, secret-free diagnostics may reach the caller."""


def read_private(path: Path) -> str:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise BootstrapError("symlink in private file path")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise BootstrapError("private input must be a regular mode-0600 file")
        if metadata.st_size > 1024 * 1024:
            raise BootstrapError("private input is too large")
        return stream.read()


def environment_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in read_private(path).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in values:
            raise BootstrapError("invalid or duplicate environment assignment")
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
            if "'" in value:
                raise BootstrapError("unsupported environment quoting")
        elif "$" in value or value.startswith('"'):
            raise BootstrapError("use single-quoted literal values; interpolation is forbidden")
        values[key] = value
    return values


def credentials(registry: str) -> tuple[str, str]:
    try:
        data = json.loads(registry)
        routes = data["routes"]
        if not isinstance(routes, list) or not 1 <= len(routes) <= 100:
            raise ValueError
        pairs = set()
        for route in routes:
            if route["qbittorrent_url"] != "http://qbittorrent:8080":
                raise ValueError
            username, password = route["qbittorrent_username"], route["qbittorrent_password"]
            # Bounded QSettings-safe usernames. Passwords are UTF-8, never INI text.
            if not isinstance(username, str) or not re.fullmatch(
                r"[A-Za-z0-9_.@-]{1,128}", username
            ):
                raise ValueError
            if (
                not isinstance(password, str)
                or not 20 <= len(password) <= 1024
                or "\x00" in password
            ):
                raise ValueError
            if any(marker in password.lower() for marker in ("replace-with", "changeme")):
                raise ValueError
            pairs.add((username, password))
        if len(pairs) != 1:
            raise ValueError
        return pairs.pop()
    except (KeyError, TypeError, ValueError):
        raise BootstrapError("registry requires one unambiguous internal qB credential") from None


def settings(text: str) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    section = ""
    sections: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            if section in sections:
                raise BootstrapError("duplicate INI section")
            sections.add(section)
        else:
            key, sep, value = line.partition("=")
            identity = (section, key.strip())
            if not section or not sep or identity in result:
                raise BootstrapError("invalid or duplicate INI setting")
            result[identity] = value.strip()
    return result


def password_matches(value: str, password: str) -> bool:
    try:
        # Qt serializes QByteArray as this quoted INI value.
        match = re.fullmatch(r'"?@ByteArray\(([A-Za-z0-9+/=]+):([A-Za-z0-9+/=]+)\)"?', value)
        if match is None:
            return False
        salt, expected = (base64.b64decode(part, validate=True) for part in match.groups())
        return (
            len(salt) == 16
            and len(expected) == 64
            and hmac.compare_digest(
                expected, hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100_000, 64)
            )
        )
    except ValueError:
        return False


def password_value(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100_000, 64)
    return (
        '"@ByteArray('
        + base64.b64encode(salt).decode()
        + ":"
        + base64.b64encode(derived).decode()
        + ')"'
    )


def render(existing: str, username: str, password: str) -> str:
    policy = settings(POLICY.read_text(encoding="utf-8"))
    if policy != REQUIRED:
        raise BootstrapError("versioned qB policy does not match the security contract")
    old = settings(existing)
    desired = dict(policy)
    desired[USERNAME] = username
    current_hash = old.get(PASSWORD, "")
    desired[PASSWORD] = (
        current_hash if password_matches(current_hash, password) else password_value(password)
    )
    # Only the generated host bootstrap is rendered here. Runtime reconciliation
    # preserves all unrelated volume settings. Never preserve legacy plaintext auth.
    old.pop(("Preferences", r"WebUI\Password"), None)
    old.pop(("Preferences", r"WebUI\Password_ha1"), None)
    old.update(desired)
    # Only the fresh-profile seed carries this marker. qB 5.2.3 upgrade.cpp
    # otherwise treats an explicit modern config as pre-v4 and overwrites its
    # proxy profiles. The runtime reconciler leaves an existing profile's Meta
    # section untouched, so genuine upstream migrations are never suppressed.
    old[("Meta", "MigrationVersion")] = "8"
    lines = []
    for section in sorted({section for section, _ in old}):
        lines.append(f"[{section}]")
        lines.extend(
            f"{key}={value}" for (group, key), value in sorted(old.items()) if group == section
        )
        lines.append("")
    return "\n".join(lines)


def write_private(target: Path, content: str, uid: int, gid: int) -> None:
    for parent in (target, *target.parents):
        if parent.is_symlink():
            raise BootstrapError("symlink in private output path")
    if target.exists():
        existing = read_private(target)
        if existing == content and (target.stat().st_uid, target.stat().st_gid) == (uid, gid):
            return
    fd, name = tempfile.mkstemp(prefix=".qb-bootstrap-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fchmod(output.fileno(), 0o600)
            os.fchown(output.fileno(), uid, gid)
            os.fsync(output.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare(environment: Path, *, check: bool = False) -> None:
    values = environment_values(environment)
    try:
        username, password = credentials(values["WOS_V2_INTEGRATION_ACCOUNTS_JSON"])
        target = Path(values["WOS_V2_QBITTORRENT_CONFIG_PATH"])
        uid, gid = int(values["WOS_V2_QBITTORRENT_UID"]), int(values["WOS_V2_QBITTORRENT_GID"])
        app_uid, app_gid = int(values["WOS_V2_APP_UID"]), int(values["WOS_V2_APP_GID"])
    except (KeyError, ValueError):
        raise BootstrapError("missing qB bootstrap environment contract") from None
    registry_target = Path(str(target) + ".integration.json")
    if (
        not target.is_absolute()
        or environment in (target, registry_target)
        or min(uid, gid, app_uid, app_gid) <= 0
    ):
        raise BootstrapError("invalid bootstrap path or identity")
    for parent in (target, *target.parents):
        if parent.is_symlink():
            raise BootstrapError("symlink in bootstrap path")
    existing = read_private(target) if target.exists() else ""
    result = render(existing, username, password)
    if check:
        actual = settings(existing)
        if (
            any(actual.get(key) != value for key, value in REQUIRED.items())
            or actual.get(("Meta", "MigrationVersion")) != "8"
            or actual.get(USERNAME) != username
            or not password_matches(actual.get(PASSWORD, ""), password)
        ):
            raise BootstrapError("qB bootstrap is absent or stale; run preflight first")
        if (target.stat().st_uid, target.stat().st_gid) != (uid, gid):
            raise BootstrapError("qB bootstrap ownership is incorrect")
        if read_private(registry_target) != values["WOS_V2_INTEGRATION_ACCOUNTS_JSON"] or (
            registry_target.stat().st_uid,
            registry_target.stat().st_gid,
        ) != (app_uid, app_gid):
            raise BootstrapError("derived integration secret is absent or stale")
        return
    write_private(registry_target, values["WOS_V2_INTEGRATION_ACCOUNTS_JSON"], app_uid, app_gid)
    write_private(target, result, uid, gid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("environment", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.environment, check=args.check)
    except (BootstrapError, OSError, UnicodeError) as error:
        message = (
            str(error) if isinstance(error, BootstrapError) else "private input/output unavailable"
        )
        print(f"qB bootstrap failed: {message}", file=sys.stderr)
        return 1
    print(
        "qB bootstrap contract verified."
        if args.check
        else "qB bootstrap prepared without plaintext credentials."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
