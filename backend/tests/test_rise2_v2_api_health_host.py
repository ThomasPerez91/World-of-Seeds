from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPOSITORY / "deploy" / ".env.rise2.v2.example"
PREFLIGHT = REPOSITORY / "scripts" / "rise2_v2_preflight.sh"
COMPOSE = REPOSITORY / "deploy" / "compose.rise2.v2.yaml"
NEWGREEDY_SMOKE = REPOSITORY / "scripts" / "rise2_v2_newgreedy_smoke.sh"


def test_rise2_example_allows_local_api_health_host() -> None:
    environment = ENV_EXAMPLE.read_text(encoding="utf-8")
    allowed_hosts = next(
        line for line in environment.splitlines() if line.startswith("WOS_V2_ALLOWED_HOSTS=")
    )

    assert '"127.0.0.1"' in allowed_hosts


def test_rise2_preflight_requires_local_api_health_host() -> None:
    script = PREFLIGHT.read_text(encoding="utf-8")

    assert "allowed_hosts=$(env_value WOS_V2_ALLOWED_HOSTS)" in script
    assert "WOS_V2_ALLOWED_HOSTS must include 127.0.0.1 for the local API healthcheck" in script


def test_rise2_api_healthcheck_targets_local_loopback() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "http://127.0.0.1:8000/api/v1/health/ready" in compose


def test_rise2_ci_smoke_allows_local_api_health_host() -> None:
    script = NEWGREEDY_SMOKE.read_text(encoding="utf-8")

    assert 'WOS_V2_ALLOWED_HOSTS=["v2-ci.example.invalid","api","127.0.0.1"]' in script
