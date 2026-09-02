import base64
import hashlib
import json
import os
import runpy
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPOSITORY / "scripts/rise2_v2_qb_bootstrap.py"


def module() -> dict[str, Any]:
    return runpy.run_path(str(BOOTSTRAP))


def registry(password: str = "disposable-test-password-$-unicode-é") -> str:
    return json.dumps(
        {
            "routes": [
                {
                    "qbittorrent_url": "http://qbittorrent:8080",
                    "qbittorrent_username": "test-user",
                    "qbittorrent_password": password,
                }
            ]
        }
    )


def test_qb_523_password_derivation_known_vector_and_roundtrip() -> None:
    ns = module()
    password = "disposable-test-password-é"
    salt = bytes(range(16))
    expected = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, 100_000, 64)
    value = (
        '"@ByteArray('
        + base64.b64encode(salt).decode()
        + ":"
        + base64.b64encode(expected).decode()
        + ')"'
    )
    assert ns["password_matches"](value, password)
    assert not ns["password_matches"](value, "incorrect")
    generated = ns["password_value"](password)
    assert password not in generated
    assert ns["password_matches"](generated, password)
    assert not ns["password_matches"]('"@ByteArray(invalid:hash)"', password)


def test_render_is_idempotent_and_preserves_unrelated_preferences() -> None:
    ns = module()
    user, password = ns["credentials"](registry())
    old = "[Preferences]\nGeneral\\Locale=fr\nWebUI\\Password=legacy-test-only\n"
    result = ns["render"](old, user, password)
    assert result == ns["render"](result, user, password)
    assert "General\\Locale=fr" in result
    assert "legacy-test-only" not in result
    assert password not in result
    assert ns["settings"](result)[("Meta", "MigrationVersion")] == "8"
    assert all(ns["settings"](result)[key] == value for key, value in ns["REQUIRED"].items())


@pytest.mark.parametrize(
    "key,value",
    [
        ("qbittorrent_url", "http://external:8080"),
        ("qbittorrent_password", ""),
        ("qbittorrent_username", "bad\nvalue"),
    ],
)
def test_invalid_registry_fails_without_echoing_credentials(key: str, value: str) -> None:
    ns = module()
    payload = json.loads(registry())
    payload["routes"][0][key] = value
    with pytest.raises(ns["BootstrapError"], match="unambiguous") as error:
        ns["credentials"](json.dumps(payload))
    assert "disposable-test" not in str(error.value)


def test_multiple_distinct_qb_credentials_fail_closed() -> None:
    ns = module()
    payload = json.loads(registry())
    payload["routes"].append(
        json.loads(registry("different-disposable-test-password"))["routes"][0]
    )
    with pytest.raises(ns["BootstrapError"]):
        ns["credentials"](json.dumps(payload))


def test_private_environment_quoting_and_no_interpolation(tmp_path: Path) -> None:
    ns = module()
    environment = tmp_path / "environment"
    environment.write_text("WOS_V2_INTEGRATION_ACCOUNTS_JSON='" + registry() + "'\n")
    environment.chmod(0o600)
    assert ns["environment_values"](environment)["WOS_V2_INTEGRATION_ACCOUNTS_JSON"] == registry()
    environment.write_text("WOS_V2_INTEGRATION_ACCOUNTS_JSON=" + registry() + "\n")
    with pytest.raises(ns["BootstrapError"], match="interpolation"):
        ns["environment_values"](environment)
    environment.chmod(0o644)
    with pytest.raises(ns["BootstrapError"], match="0600"):
        ns["environment_values"](environment)


@pytest.mark.parametrize(
    "setting,replacement",
    [
        ("HostHeaderValidation=true", "HostHeaderValidation=false"),
        ("CSRFProtection=true", "CSRFProtection=false"),
        ("ServerDomains=qbittorrent;localhost", "ServerDomains=*"),
        ("ProxyPeerConnections=false", "ProxyPeerConnections=true"),
    ],
)
def test_policy_cannot_weaken_auth_or_proxy_boundaries(setting: str, replacement: str) -> None:
    ns = module()
    text = ns["POLICY"].read_text().replace(setting, replacement)
    assert ns["settings"](text) != ns["REQUIRED"]


def reconcile(
    tmp_path: Path, existing: str | None
) -> tuple[subprocess.CompletedProcess[str], Path]:
    ns = module()
    inputs = tmp_path / "bootstrap"
    inputs.mkdir(exist_ok=True)
    (inputs / "policy.conf").write_text(ns["POLICY"].read_text())
    user, password = ns["credentials"](registry())
    (inputs / "qBittorrent.conf").write_text(ns["render"]("", user, password))
    (inputs / "reconcile.awk").write_text(
        (REPOSITORY / "scripts/rise2_v2_qb_reconcile.awk").read_text()
    )
    profile = tmp_path / "config/qBittorrent/config/qBittorrent.conf"
    profile.parent.mkdir(parents=True, exist_ok=True)
    if existing is not None:
        profile.write_text(existing)
    result = subprocess.run(
        ["sh", str(REPOSITORY / "scripts/rise2_v2_qb_reconcile.sh")],
        env={
            **os.environ,
            "QBT_BOOTSTRAP_DIR": str(inputs),
            "QBT_CONFIG_ROOT": str(tmp_path / "config"),
            "QBT_UID": "10001",
            "QBT_GID": "10002",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result, profile


@pytest.mark.skipif(
    os.geteuid() != 0,
    reason="profile ownership regression requires root; Docker smoke covers this in CI",
)
@pytest.mark.parametrize(
    "existing",
    [
        None,
        "[Preferences]\nGeneral\\Locale=fr\nWebUI\\HostHeaderValidation=false\n[BitTorrent]\nSession\\ProxyPeerConnections=true\n",
    ],
)
def test_real_shell_reconciliation_fresh_and_existing(tmp_path: Path, existing: str | None) -> None:
    ownership_probe = tmp_path / "ownership-probe"
    ownership_probe.touch()
    try:
        os.chown(ownership_probe, 10001, 10002)
    except OSError:
        pytest.skip("container UID mapping unavailable here; real Docker smoke is mandatory")
    result, profile = reconcile(tmp_path, existing)
    assert result.returncode == 0, result.stderr
    ns = module()
    settings = ns["settings"](profile.read_text())
    assert all(settings[key] == value for key, value in ns["REQUIRED"].items())
    assert ns["password_matches"](
        settings[ns["PASSWORD"]], json.loads(registry())["routes"][0]["qbittorrent_password"]
    )
    assert profile.stat().st_mode & 0o777 == 0o600
    if existing:
        assert settings[("Preferences", r"General\Locale")] == "fr"


def test_duplicate_ini_refused_without_values_in_error() -> None:
    ns = module()
    with pytest.raises(ns["BootstrapError"], match="duplicate"):
        ns["settings"]("[Preferences]\nkey=private-test\nkey=other-test\n")


def test_private_derived_file_is_atomic_idempotent_and_rejects_symlinks(tmp_path: Path) -> None:
    ns = module()
    target = tmp_path / "registry.json"
    ns["write_private"](target, registry(), os.getuid(), os.getgid())
    inode = target.stat().st_ino
    assert target.stat().st_mode & 0o777 == 0o600
    ns["write_private"](target, registry(), os.getuid(), os.getgid())
    assert target.stat().st_ino == inode
    changed = registry("rotated-disposable-test-password")
    ns["write_private"](target, changed, os.getuid(), os.getgid())
    assert target.read_text() == changed
    assert target.stat().st_ino != inode
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ns["BootstrapError"], match="symlink"):
        ns["write_private"](link, "not-written", os.getuid(), os.getgid())
    assert target.read_text() == changed


def test_awk_reconciles_and_is_idempotent_without_root(tmp_path: Path) -> None:
    ns = module()
    username, password = ns["credentials"](registry())
    bootstrap = tmp_path / "bootstrap.conf"
    bootstrap.write_text(ns["render"]("", username, password))
    existing = tmp_path / "existing.conf"
    existing.write_text(
        "[Preferences]\nGeneral\\Locale=fr\nWebUI\\Password=legacy-test-only\n"
        "WebUI\\HostHeaderValidation=false\n[BitTorrent]\nSession\\MaxConnections=137\n"
        "[Meta]\nMigrationVersion=7\n"
    )
    command = [
        "awk",
        "-f",
        str(REPOSITORY / "scripts/rise2_v2_qb_reconcile.awk"),
        str(ns["POLICY"]),
        str(bootstrap),
        str(existing),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    assert "legacy-test-only" not in result
    assert "General\\Locale=fr" in result
    assert "Session\\MaxConnections=137" in result
    assert ns["settings"](result)[("Meta", "MigrationVersion")] == "7"
    assert all(ns["settings"](result)[key] == value for key, value in ns["REQUIRED"].items())
    existing.write_text(result)
    assert subprocess.run(command, capture_output=True, text=True, check=True).stdout == result
    bootstrap.write_text(
        bootstrap.read_text().replace("CSRFProtection=true", "CSRFProtection=false")
    )
    rejected = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rejected.returncode != 0
    assert password not in rejected.stderr


@pytest.mark.parametrize("process", ["app.worker", "app.scheduler_service"])
def test_integration_entrypoint_injects_secret_only_into_authorized_process(
    monkeypatch: pytest.MonkeyPatch, process: str
) -> None:
    import sys

    expected = registry()
    monkeypatch.setattr(sys, "argv", ["integration-entrypoint.py", process])
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: expected)
    monkeypatch.setenv("WOS_INTEGRATION_ACCOUNTS_JSON", "before-test")
    launched: list[list[str]] = []
    monkeypatch.setattr(os, "execv", lambda executable, args: launched.append(args))
    runpy.run_path(str(REPOSITORY / "scripts/rise2_v2_integration_entrypoint.py"))
    assert os.environ["WOS_INTEGRATION_ACCOUNTS_JSON"] == expected
    assert launched == [[sys.executable, "-m", process]]
