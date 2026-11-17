#!/usr/bin/env python3
"""Render the private Rise2 Alertmanager configuration from the versioned template.

The private directory (default /etc/world-of-seeds-v2/alertmanager) holds:

- settings.json: {"smtp_smarthost", "smtp_from", "smtp_auth_username", "email_to": [...]}
- discord_webhook_url: the Discord webhook URL, without a trailing newline
- smtp_password: the SMTP password, without a trailing newline

It receives the rendered alertmanager.yml. Alertmanager runs as nobody (65534) and mounts the
directory read-only; secrets stay in their files and are read by Alertmanager at send time.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from string import Template

ALERTMANAGER_GID = 65534
SETTINGS = "settings.json"
OUTPUT = "alertmanager.yml"
SECRETS = ("discord_webhook_url", "smtp_password")
SETTING_KEYS = {"smtp_smarthost", "smtp_from", "smtp_auth_username", "email_to"}

_HOST_PORT = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?:[0-9]{1,5}")
_ADDRESS = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_DISPLAY_FROM = re.compile(rf"[A-Za-z0-9 ._-]{{1,64}} <{_ADDRESS.pattern}>")
_USERNAME = re.compile(r"[A-Za-z0-9._%+@-]{1,254}")
_DISCORD_WEBHOOK = re.compile(
    r"https://(?:discord|discordapp)\.com/api/webhooks/[0-9]{1,30}/[A-Za-z0-9_-]{1,200}"
)


class AlertmanagerRenderError(RuntimeError):
    """Raised when the private alerting settings are missing or unsafe."""


def _setting(
    settings: dict[str, object], key: str, pattern: re.Pattern[str], *alt: re.Pattern[str]
) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not any(p.fullmatch(value) for p in (pattern, *alt)):
        raise AlertmanagerRenderError(f"settings.json: invalid {key}")
    return value


def load_settings(raw: str) -> dict[str, str]:
    try:
        settings = json.loads(raw)
    except json.JSONDecodeError:
        raise AlertmanagerRenderError("settings.json is not valid JSON") from None
    if not isinstance(settings, dict) or set(settings) != SETTING_KEYS:
        raise AlertmanagerRenderError(
            "settings.json must hold exactly: " + ", ".join(sorted(SETTING_KEYS))
        )
    smarthost = _setting(settings, "smtp_smarthost", _HOST_PORT)
    if not 1 <= int(smarthost.rsplit(":", 1)[1]) <= 65535:
        raise AlertmanagerRenderError("settings.json: invalid smtp_smarthost")
    recipients = settings["email_to"]
    if (
        not isinstance(recipients, list)
        or not 1 <= len(recipients) <= 10
        or not all(isinstance(item, str) and _ADDRESS.fullmatch(item) for item in recipients)
    ):
        raise AlertmanagerRenderError("settings.json: email_to must list 1 to 10 addresses")
    return {
        "smtp_smarthost": smarthost,
        "smtp_from": _setting(settings, "smtp_from", _ADDRESS, _DISPLAY_FROM),
        "smtp_auth_username": _setting(settings, "smtp_auth_username", _USERNAME),
        "email_to": ", ".join(recipients),
    }


def render(template: str, settings: dict[str, str]) -> str:
    # JSON strings are valid YAML double-quoted scalars, so no value can break the structure.
    quoted = {key: json.dumps(value) for key, value in settings.items()}
    try:
        return Template(template).substitute(quoted)
    except (KeyError, ValueError) as exc:
        raise AlertmanagerRenderError("template placeholders do not match the settings") from exc


def _private_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise AlertmanagerRenderError(f"{path.name} is missing")
    if path.stat().st_mode & 0o007:
        raise AlertmanagerRenderError(f"{path.name} must not be readable by other users")
    return path.read_text(encoding="utf-8")


def check_secrets(directory: Path) -> None:
    webhook = _private_file(directory / "discord_webhook_url")
    if not _DISCORD_WEBHOOK.fullmatch(webhook):
        raise AlertmanagerRenderError(
            "discord_webhook_url must hold one Discord webhook URL, without a trailing newline"
        )
    password = _private_file(directory / "smtp_password")
    if not password or password != password.strip():
        raise AlertmanagerRenderError(
            "smtp_password must not be empty or end with whitespace (use printf '%s')"
        )


def _share_with_alertmanager(path: Path, mode: int, *, ownership: bool) -> None:
    if ownership:
        os.chown(path, 0, ALERTMANAGER_GID, follow_symlinks=False)
    os.chmod(path, mode, follow_symlinks=False)


def render_directory(directory: Path, template_path: Path, *, ownership: bool) -> Path:
    if directory.is_symlink() or not directory.is_dir():
        raise AlertmanagerRenderError("the private Alertmanager directory is missing")
    if directory.stat().st_mode & 0o007:
        raise AlertmanagerRenderError(
            "the private Alertmanager directory must not be world-readable"
        )
    settings = load_settings(_private_file(directory / SETTINGS))
    check_secrets(directory)
    rendered = render(template_path.read_text(encoding="utf-8"), settings)

    output = directory / OUTPUT
    if output.is_symlink():
        raise AlertmanagerRenderError("alertmanager.yml must not be a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=".alertmanager.", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        _share_with_alertmanager(Path(temporary), 0o640, ownership=ownership)
        os.replace(temporary, output)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    _share_with_alertmanager(directory, 0o750, ownership=ownership)
    for name in SECRETS:
        _share_with_alertmanager(directory / name, 0o640, ownership=ownership)
    if not stat.S_ISREG(output.lstat().st_mode):
        raise AlertmanagerRenderError("rendered configuration is not a regular file")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--directory", type=Path, default=Path("/etc/world-of-seeds-v2/alertmanager")
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "monitoring/alertmanager/alertmanager.yml.template",
    )
    parser.add_argument(
        "--skip-ownership",
        action="store_true",
        help="keep file owners (CI and tests); production runs as root and shares with nobody",
    )
    args = parser.parse_args()
    try:
        output = render_directory(args.directory, args.template, ownership=not args.skip_ownership)
    except (AlertmanagerRenderError, OSError, UnicodeDecodeError) as exc:
        print(f"Alertmanager configuration not rendered: {exc}", file=sys.stderr)
        return 1
    print(f"Rendered {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
