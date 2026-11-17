"""Rise2 Alertmanager: private rendering, overlay isolation and Prometheus wiring."""

from __future__ import annotations

import json
import re
import runpy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
TEMPLATE = REPOSITORY / "monitoring/alertmanager/alertmanager.yml.template"
WEBHOOK = "https://discord.com/api/webhooks/123456789012345678/abc_DEF-123"
SETTINGS = {
    "smtp_smarthost": "smtp.example.org:587",
    "smtp_from": "World of Seeds <alerts@example.org>",
    "smtp_auth_username": "alerts@example.org",
    "email_to": ["ops@example.org", "second@example.org"],
}


_NAMESPACE: dict[str, Any] = {}


def _renderer() -> dict[str, Any]:
    # One namespace per test session, so the raised and the expected error are the same class.
    if not _NAMESPACE:
        _NAMESPACE.update(
            runpy.run_path(str(REPOSITORY / "scripts/rise2_v2_alertmanager_render.py"))
        )
    return _NAMESPACE


def _private(directory: Path, name: str, content: str) -> None:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


@pytest.fixture
def private_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "alertmanager"
    directory.mkdir(mode=0o750)
    directory.chmod(0o750)
    _private(directory, "settings.json", json.dumps(SETTINGS))
    _private(directory, "discord_webhook_url", WEBHOOK)
    _private(directory, "smtp_password", "s3cret pass")
    return directory


def _render(directory: Path) -> Path:
    namespace = _renderer()
    render_directory: Callable[..., Path] = namespace["render_directory"]
    return render_directory(directory, TEMPLATE, ownership=False)


def _error() -> type[Exception]:
    error: type[Exception] = _renderer()["AlertmanagerRenderError"]
    return error


def test_template_placeholders_match_the_private_settings_exactly() -> None:
    placeholders = set(re.findall(r"\$\{([a-z_]+)\}", TEMPLATE.read_text(encoding="utf-8")))
    assert placeholders == _renderer()["SETTING_KEYS"]
    assert "$" not in re.sub(r"\$\{[a-z_]+\}", "", TEMPLATE.read_text(encoding="utf-8"))


def test_render_quotes_values_and_shares_files_only_with_the_container_group(
    private_directory: Path,
) -> None:
    output = _render(private_directory)

    rendered = output.read_text(encoding="utf-8")
    assert 'smtp_smarthost: "smtp.example.org:587"' in rendered
    assert 'smtp_from: "World of Seeds <alerts@example.org>"' in rendered
    assert 'to: "ops@example.org, second@example.org"' in rendered
    assert "${" not in rendered
    assert WEBHOOK not in rendered
    assert "s3cret" not in rendered
    assert rendered.count("webhook_url_file: /etc/alertmanager/private/discord_webhook_url") == 2
    assert "smtp_auth_password_file: /etc/alertmanager/private/smtp_password" in rendered
    assert "smtp_require_tls: true" in rendered
    assert private_directory.stat().st_mode & 0o777 == 0o750
    for name in ("alertmanager.yml", "discord_webhook_url", "smtp_password"):
        assert (private_directory / name).stat().st_mode & 0o777 == 0o640
    assert (private_directory / "settings.json").stat().st_mode & 0o777 == 0o600
    assert [path.name for path in private_directory.iterdir() if path.name.startswith(".")] == []


def test_template_routes_critical_to_discord_and_email_and_silences_the_watchdog() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    routes = template.split("routes:", 1)[1].split("receivers:", 1)[0]
    assert "receiver: discord\n" in template.split("routes:", 1)[0]
    assert routes.index("receiver: blackhole") < routes.index("receiver: discord-and-email")
    assert "'alertname = \"Watchdog\"'" in routes
    assert "'severity = \"critical\"'" in routes
    receivers = template.split("receivers:", 1)[1]
    assert "  - name: blackhole\n  - name: discord\n" in receivers
    assert receivers.count("send_resolved: true") == 3


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("smtp_smarthost", "smtp.example.org"),
        ("smtp_smarthost", "smtp.example.org:0"),
        ("smtp_smarthost", "smtp.example.org:587\nsmtp_require_tls: false"),
        ("smtp_from", 'alerts@example.org"\nsmtp_require_tls: false'),
        ("smtp_from", "{{ .CommonLabels }} <alerts@example.org>"),
        ("smtp_auth_username", "user name"),
        ("email_to", "ops@example.org"),
        ("email_to", []),
        ("email_to", ["not-an-address"]),
        ("email_to", ["ops@example.org, injected@example.org"]),
    ],
)
def test_render_rejects_unsafe_settings(private_directory: Path, key: str, value: object) -> None:
    _private(private_directory, "settings.json", json.dumps({**SETTINGS, key: value}))

    with pytest.raises(_error(), match=key):
        _render(private_directory)
    assert not (private_directory / "alertmanager.yml").exists()


@pytest.mark.parametrize(
    "settings",
    [
        "not json",
        json.dumps({key: value for key, value in SETTINGS.items() if key != "email_to"}),
        json.dumps({**SETTINGS, "smtp_require_tls": False}),
    ],
)
def test_render_requires_exactly_the_documented_settings(
    private_directory: Path, settings: str
) -> None:
    _private(private_directory, "settings.json", settings)

    with pytest.raises(_error(), match="settings.json"):
        _render(private_directory)


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("discord_webhook_url", WEBHOOK + "\n", "discord_webhook_url"),
        ("discord_webhook_url", "https://example.org/hook", "discord_webhook_url"),
        ("smtp_password", "", "smtp_password"),
        ("smtp_password", "secret\n", "smtp_password"),
    ],
)
def test_render_rejects_malformed_secret_files(
    private_directory: Path, name: str, content: str, message: str
) -> None:
    _private(private_directory, name, content)

    with pytest.raises(_error(), match=message):
        _render(private_directory)


@pytest.mark.parametrize("name", ["settings.json", "discord_webhook_url", "smtp_password"])
def test_render_refuses_files_readable_by_other_users(private_directory: Path, name: str) -> None:
    (private_directory / name).chmod(0o644)

    with pytest.raises(_error(), match="readable by other users"):
        _render(private_directory)


def test_render_refuses_a_world_readable_directory(private_directory: Path) -> None:
    private_directory.chmod(0o755)

    with pytest.raises(_error(), match="world-readable"):
        _render(private_directory)


def test_render_never_follows_a_symlinked_output(private_directory: Path, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.yml"
    (private_directory / "alertmanager.yml").symlink_to(target)

    with pytest.raises(_error(), match="symlink"):
        _render(private_directory)
    assert not target.exists()


def _service_block(compose: str, name: str) -> str:
    return compose.split(f"\n  {name}:\n", 1)[1].split("\n\n", 1)[0]


def test_overlay_runs_a_pinned_isolated_alertmanager_with_its_own_egress() -> None:
    overlay = (REPOSITORY / "deploy/compose.rise2.observability.v2.yaml").read_text(
        encoding="utf-8"
    )
    block = _service_block(overlay, "alertmanager")

    assert "image: prom/alertmanager:v0.34.2" in block
    assert "networks: [monitoring, alerting-egress]" in block
    assert "--cluster.listen-address=" in block
    assert "source: /etc/world-of-seeds-v2/alertmanager" in block
    assert "read_only: true" in block and "bind: {create_host_path: false}" in block
    assert "cap_drop: [ALL]" in block
    assert "security_opt: [no-new-privileges:true]" in block
    assert "ports:" not in block and "privileged" not in block
    assert "\nnetworks:\n  alerting-egress: {}\n" in overlay
    assert "\nvolumes:\n  alertmanager_v2_data:\n" in overlay


def test_rise2_prometheus_sends_alerts_and_scrapes_alertmanager() -> None:
    prometheus = (REPOSITORY / "monitoring/prometheus/prometheus.rise2.yml").read_text(
        encoding="utf-8"
    )
    local = (REPOSITORY / "monitoring/prometheus/prometheus.yml").read_text(encoding="utf-8")

    assert "alerting:\n  alertmanagers:\n    - static_configs:\n" in prometheus
    assert "        - targets: [alertmanager:9093]\n" in prometheus
    assert "job_name: alertmanager" in prometheus
    assert "alertmanager" not in local


def test_every_alert_severity_has_a_route() -> None:
    alerts = (REPOSITORY / "monitoring/prometheus/alerts.yml").read_text(encoding="utf-8")
    severities = set(re.findall(r"severity: ([a-z]+)", alerts))

    assert severities == {"critical", "warning", "none"}
    assert alerts.count("severity: none") == 1
    assert "      - alert: Watchdog\n        expr: vector(1)\n" in alerts


def test_apply_script_renders_checks_then_recreates_alertmanager() -> None:
    script = (REPOSITORY / "scripts/rise2_v2_observability_apply.sh").read_text(encoding="utf-8")

    render = script.index("rise2_v2_alertmanager_render.py")
    check = script.index("--entrypoint amtool alertmanager")
    recreate = script.index("compose up -d --force-recreate --no-deps")
    assert render < check < recreate
    assert "  alertmanager \\\n" in script[recreate:]
