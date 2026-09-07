from pathlib import Path


def test_rise2_ingress_keeps_metrics_private_and_security_headers_uniform() -> None:
    repository = Path(__file__).resolve().parents[2]
    caddy = (repository / "deploy/Caddyfile.rise2.v2").read_text(encoding="utf-8")

    assert "@private_metrics path /api/v2/metrics" in caddy
    assert "respond @private_metrics 404" in caddy
    assert 'Referrer-Policy "no-referrer"' in caddy
    assert "header_up X-Forwarded-For {remote_host}" in caddy
    assert "same-origin" not in caddy
