import json
import os
import runpy
import stat
from pathlib import Path
from typing import Any

import pytest


def _renderer() -> dict[str, Any]:
    repository = Path(__file__).resolve().parents[2]
    return runpy.run_path(str(repository / "scripts/rise2_v2_qbittorrent_bootstrap.py"))


def _compose_config(*, password: str = "correct-horse-battery-staple") -> dict[str, Any]:
    registry = {
        "routes": [
            {
                "tracker_account_ref": "11111111-1111-4111-8111-111111111111",
                "qbittorrent_account_ref": "22222222-2222-4222-8222-222222222222",
                "newgreedy_url": "http://newgreedy:8080",
                "c411_passkey": "not-used-by-bootstrap",
                "qbittorrent_url": "http://qbittorrent:8080",
                "qbittorrent_username": "wos-v2",
                "qbittorrent_password": password,
            }
        ]
    }
    return {
        "services": {
            "scheduler": {
                "environment": {
                    "WOS_INTEGRATION_ACCOUNTS_JSON": json.dumps(registry, separators=(",", ":"))
                }
            }
        }
    }


def test_qbittorrent_bootstrap_renders_required_safe_runtime_settings(tmp_path: Path) -> None:
    namespace = _renderer()
    ensure_bootstrap = namespace["ensure_bootstrap"]
    parse_ini = namespace["_parse_ini"]
    verify_password = namespace["_verify_password"]

    output = tmp_path / "qBittorrent.conf"
    changed = ensure_bootstrap(
        _compose_config(),
        output,
        uid=os.getuid(),
        gid=os.getgid(),
        salt=b"\x11" * 16,
    )

    assert changed is True
    assert stat.S_IMODE(output.stat().st_mode) == 0o600

    values = parse_ini(output.read_text(encoding="utf-8"))
    assert values[("Preferences", r"WebUI\Username")] == "wos-v2"
    assert values[("Preferences", r"WebUI\ServerDomains")] == "qbittorrent"
    assert values[("Preferences", r"WebUI\HostHeaderValidation")] == "true"
    assert values[("Preferences", r"WebUI\CSRFProtection")] == "true"
    assert values[("Preferences", r"WebUI\LocalHostAuth")] == "false"
    assert values[("BitTorrent", r"Session\DefaultSavePath")] == "/data"
    assert values[("Network", r"Proxy\Type")] == "HTTP"
    assert values[("Network", r"Proxy\IP")] == "newgreedy"
    assert values[("Network", r"Proxy\Port")] == "3456"
    assert values[("Network", r"Proxy\Profiles\BitTorrent")] == "true"
    assert values[("BitTorrent", r"Session\ProxyPeerConnections")] == "false"
    assert values[("Network", r"Proxy\Profiles\RSS")] == "false"
    assert values[("Network", r"Proxy\Profiles\Misc")] == "false"

    stored_hash = values[("Preferences", r"WebUI\Password_PBKDF2")]
    assert verify_password(stored_hash, "correct-horse-battery-staple") is True
    assert "correct-horse-battery-staple" not in output.read_text(encoding="utf-8")
    assert "not-used-by-bootstrap" not in output.read_text(encoding="utf-8")


def test_qbittorrent_bootstrap_is_idempotent_when_registry_is_unchanged(tmp_path: Path) -> None:
    namespace = _renderer()
    ensure_bootstrap = namespace["ensure_bootstrap"]
    output = tmp_path / "qBittorrent.conf"
    config = _compose_config()

    assert (
        ensure_bootstrap(
            config,
            output,
            uid=os.getuid(),
            gid=os.getgid(),
            salt=b"\x22" * 16,
        )
        is True
    )
    first = output.read_bytes()

    assert (
        ensure_bootstrap(
            config,
            output,
            uid=os.getuid(),
            gid=os.getgid(),
            salt=b"\x33" * 16,
        )
        is False
    )
    assert output.read_bytes() == first


def test_qbittorrent_bootstrap_reconciles_password_rotation_without_plaintext(
    tmp_path: Path,
) -> None:
    namespace = _renderer()
    ensure_bootstrap = namespace["ensure_bootstrap"]
    parse_ini = namespace["_parse_ini"]
    verify_password = namespace["_verify_password"]
    output = tmp_path / "qBittorrent.conf"

    ensure_bootstrap(
        _compose_config(password="first-password"),
        output,
        uid=os.getuid(),
        gid=os.getgid(),
        salt=b"\x44" * 16,
    )
    first = output.read_bytes()

    assert (
        ensure_bootstrap(
            _compose_config(password="second-password"),
            output,
            uid=os.getuid(),
            gid=os.getgid(),
            salt=b"\x55" * 16,
        )
        is True
    )
    second = output.read_bytes()
    assert second != first

    values = parse_ini(second.decode("utf-8"))
    stored_hash = values[("Preferences", r"WebUI\Password_PBKDF2")]
    assert verify_password(stored_hash, "second-password") is True
    assert verify_password(stored_hash, "first-password") is False
    assert b"second-password" not in second


def test_qbittorrent_bootstrap_rejects_inconsistent_local_credentials(tmp_path: Path) -> None:
    namespace = _renderer()
    ensure_bootstrap = namespace["ensure_bootstrap"]
    bootstrap_error = namespace["BootstrapError"]

    config = _compose_config()
    registry = json.loads(
        config["services"]["scheduler"]["environment"]["WOS_INTEGRATION_ACCOUNTS_JSON"]
    )
    second = dict(registry["routes"][0])
    second["qbittorrent_account_ref"] = "33333333-3333-4333-8333-333333333333"
    second["qbittorrent_password"] = "different-password"
    registry["routes"].append(second)
    config["services"]["scheduler"]["environment"]["WOS_INTEGRATION_ACCOUNTS_JSON"] = json.dumps(
        registry, separators=(",", ":")
    )

    with pytest.raises(bootstrap_error, match="share one qBittorrent WebUI credential"):
        ensure_bootstrap(
            config,
            tmp_path / "qBittorrent.conf",
            uid=os.getuid(),
            gid=os.getgid(),
            salt=b"\x66" * 16,
        )


def test_qbittorrent_bootstrap_rejects_non_internal_qbittorrent_url(tmp_path: Path) -> None:
    namespace = _renderer()
    ensure_bootstrap = namespace["ensure_bootstrap"]
    bootstrap_error = namespace["BootstrapError"]

    config = _compose_config()
    registry = json.loads(
        config["services"]["scheduler"]["environment"]["WOS_INTEGRATION_ACCOUNTS_JSON"]
    )
    registry["routes"][0]["qbittorrent_url"] = "http://example.invalid:8080"
    config["services"]["scheduler"]["environment"]["WOS_INTEGRATION_ACCOUNTS_JSON"] = json.dumps(
        registry, separators=(",", ":")
    )

    with pytest.raises(bootstrap_error, match="internal qBittorrent service"):
        ensure_bootstrap(
            config,
            tmp_path / "qBittorrent.conf",
            uid=os.getuid(),
            gid=os.getgid(),
            salt=b"\x77" * 16,
        )
