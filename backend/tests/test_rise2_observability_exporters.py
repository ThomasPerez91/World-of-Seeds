from pathlib import Path


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def test_rise2_observability_overlay_keeps_exporters_internal_and_pinned() -> None:
    overlay = (
        _repository() / "deploy" / "compose.rise2.observability.v2.yaml"
    ).read_text(encoding="utf-8")

    for image in (
        "quay.io/prometheuscommunity/postgres-exporter:v0.20.1",
        "oliver006/redis_exporter:v1.89.0",
        "quay.io/prometheus/blackbox-exporter:v0.28.0",
    ):
        assert image in overlay

    assert "postgres-exporter:" in overlay
    assert "redis-exporter:" in overlay
    assert "blackbox-exporter:" in overlay
    assert "networks: [backend, monitoring]" in overlay
    assert "networks: [backend, torrent, monitoring]" in overlay
    assert "ports:" not in overlay
    assert "cap_drop: [ALL]" in overlay
    assert "security_opt: [no-new-privileges:true]" in overlay


def test_rise2_smart_metrics_use_node_exporter_textfile_without_privileged_container() -> None:
    overlay = (
        _repository() / "deploy" / "compose.rise2.observability.v2.yaml"
    ).read_text(encoding="utf-8")
    apply_script = (
        _repository() / "scripts" / "rise2_v2_observability_apply.sh"
    ).read_text(encoding="utf-8")

    assert "--collector.textfile.directory=/var/lib/node-exporter/textfile" in overlay
    assert "WOS_V2_NODE_EXPORTER_TEXTFILE_PATH" in overlay
    assert "privileged:" not in overlay
    assert "systemctl start world-of-seeds-v2-smart-metrics.timer" in apply_script
    assert "systemctl enable" not in apply_script


def test_prometheus_scrapes_datastores_and_internal_http_probes() -> None:
    prometheus = (
        _repository() / "monitoring" / "prometheus" / "prometheus.yml"
    ).read_text(encoding="utf-8")

    for job in ("postgres-exporter", "redis-exporter", "blackbox-http"):
        assert f"job_name: {job}" in prometheus
    assert "http://api:8000/api/v1/health/ready" in prometheus
    assert "http://newgreedy:8080/api/health" in prometheus
    assert "http://qbittorrent:8080/" in prometheus
