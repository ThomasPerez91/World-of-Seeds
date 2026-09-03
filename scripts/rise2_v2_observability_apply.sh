#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this helper as root." >&2
  exit 1
fi

environment="${1:-/etc/world-of-seeds-v2/environment}"
repository="${WOS_V2_REPOSITORY:-/opt/world-of-seeds-v2}"
compose_file="$repository/deploy/compose.rise2.v2.yaml"
overlay_file="$repository/deploy/compose.rise2.observability.v2.yaml"
textfile_dir="/var/lib/world-of-seeds-v2/node-exporter-textfile"
collector_dir="/usr/local/lib/world-of-seeds-v2"
collector="$collector_dir/rise2_v2_smart_metrics.py"

[ -f "$environment" ] || { echo "Environment file not found." >&2; exit 1; }
[ -f "$compose_file" ] || { echo "Rise2 compose file not found." >&2; exit 1; }
[ -f "$overlay_file" ] || { echo "Observability overlay not found." >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker is required." >&2; exit 1; }
command -v smartctl >/dev/null 2>&1 || { echo "smartctl is required." >&2; exit 1; }

install -d -m 0755 "$textfile_dir" "$collector_dir"
install -m 0755 "$repository/scripts/rise2_v2_smart_metrics.py" "$collector"
install -m 0644 \
  "$repository/deploy/world-of-seeds-v2-smart-metrics.service" \
  /etc/systemd/system/world-of-seeds-v2-smart-metrics.service
install -m 0644 \
  "$repository/deploy/world-of-seeds-v2-smart-metrics.timer" \
  /etc/systemd/system/world-of-seeds-v2-smart-metrics.timer
systemctl daemon-reload

# Seed the textfile before node-exporter is recreated. A collection failure is
# kept visible through wos_smart_collect_success=0 instead of hiding the issue.
python3 "$collector" --output "$textfile_dir/wos_smart.prom" || true
systemctl start world-of-seeds-v2-smart-metrics.timer

compose() {
  docker compose \
    --env-file "$environment" \
    -f "$compose_file" \
    -f "$overlay_file" \
    "$@"
}

compose config --quiet
compose up -d --force-recreate --no-deps \
  node-exporter \
  postgres-exporter \
  redis-exporter \
  blackbox-exporter \
  prometheus \
  grafana

echo "Rise2 V2 observability exporters applied."
