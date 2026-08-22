#!/bin/sh
set -eu

repository=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
environment="$repository/.env.v2.local"
if [ ! -f "$environment" ]; then
  environment="$repository/.env.v2.local.example"
fi

compose() {
  docker compose --project-name world-of-seeds-v2-local \
    --env-file "$environment" \
    -f "$repository/compose.v2.yaml" \
    -f "$repository/compose.v2.local.yaml" "$@"
}

monitoring_compose() {
  docker compose --project-name world-of-seeds-v2-local \
    --env-file "$environment" \
    -f "$repository/compose.v2.yaml" \
    -f "$repository/compose.v2.local.yaml" \
    -f "$repository/compose.v2.monitoring.yaml" \
    --profile monitoring "$@"
}

case "${1:-}" in
  up)
    version=$(python3 "$repository/scripts/versioning.py" check --channel v2 --print-version)
    compose config --format json | python3 "$repository/scripts/validate_compose_v2_local.py"
    compose build --build-arg "WOS_APP_VERSION=$version"
    compose up --detach --no-build --wait --wait-timeout 180
    ;;
  smoke)
    python3 "$repository/scripts/smoke_v2_local.py"
    ;;
  monitoring-up)
    monitoring_compose config --format json \
      | python3 "$repository/scripts/validate_compose_v2_monitoring.py"
    monitoring_compose up --detach --no-build --wait --wait-timeout 180 \
      prometheus grafana node-exporter cadvisor
    ;;
  monitoring-smoke)
    python3 "$repository/scripts/smoke_v2_monitoring.py"
    ;;
  status)
    monitoring_compose ps
    ;;
  down)
    monitoring_compose down --volumes --remove-orphans
    ;;
  *)
    echo "Usage: scripts/local_v2.sh {up|smoke|monitoring-up|monitoring-smoke|status|down}" >&2
    exit 2
    ;;
esac
