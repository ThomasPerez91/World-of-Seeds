#!/usr/bin/env bash

set -Eeuo pipefail

readonly repository="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly compose_file="$repository/deploy/compose.rise2.v2.yaml"
readonly run_suffix="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
readonly project_name="world-of-seeds-v2-rise2-smoke-$run_suffix"
readonly smoke_root="/srv/world-of-seeds-v2/ci-newgreedy-$run_suffix"
readonly environment="$smoke_root/environment"
readonly state_dir="$smoke_root/newgreedy-state"
readonly config_file="$smoke_root/newgreedy/config.ini"
readonly qbittorrent_config="$smoke_root/qBittorrent.conf"
readonly wos_image="ghcr.io/thomasperez91/world-of-seeds-v2@sha256:ac883e493d4ad12fed7ab88a42b3911196365cca09d9ebe1364f5011fe431d43"
readonly newgreedy_image="ghcr.io/thomasperez91/world-of-seeds-newgreedy@sha256:7f737a5133ac71b1b346df93e6fad11ef2d2744ab3021870aae634227ad9429f"

compose() {
    sudo docker compose --project-name "$project_name" \
        --env-file "$environment" -f "$compose_file" "$@"
}

cleanup() {
    set +e
    if [[ -f "$environment" ]]; then
        compose down --volumes --remove-orphans >/dev/null 2>&1
    fi
    case "$smoke_root" in
        /srv/world-of-seeds-v2/ci-newgreedy-*) sudo rm -rf -- "$smoke_root" ;;
    esac
}
trap cleanup EXIT

fail() {
    echo "Rise2 NewGreedy smoke failed: $*" >&2
    exit 1
}

wait_healthy() {
    local container_id
    local status

    container_id="$(compose ps --quiet newgreedy)"
    [[ -n "$container_id" ]] || fail "NewGreedy container is missing"
    for _ in {1..60}; do
        status="$(sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id")"
        case "$status" in
            healthy) return 0 ;;
            unhealthy)
                compose logs newgreedy >&2
                fail "NewGreedy became unhealthy"
                ;;
        esac
        sleep 2
    done
    compose logs newgreedy >&2
    fail "NewGreedy did not become healthy"
}

cleanup
sudo install -d -o 0 -g 0 -m 0755 /srv/world-of-seeds-v2
sudo install -d -o 0 -g 0 -m 0755 "$smoke_root" "$smoke_root/newgreedy"
sudo install -d -o 0 -g 0 -m 0700 "$state_dir"
sudo install -d -o 10001 -g 10001 -m 0750 "$smoke_root/data"

sudo tee "$config_file" >/dev/null <<'EOF'
[proxy]
listen_port = 3456

[web]
web_enabled = true
web_host = 0.0.0.0
web_port = 8080
EOF
sudo chown 10001:10003 "$config_file"
sudo chmod 0640 "$config_file"

sudo tee "$qbittorrent_config" >/dev/null <<'EOF'
[Preferences]
WebUI\Enabled=true
EOF
sudo chown 10001:10002 "$qbittorrent_config"
sudo chmod 0600 "$qbittorrent_config"

sudo tee "$environment" >/dev/null <<EOF
WOS_V2_IMAGE=$wos_image
WOS_V2_NEWGREEDY_IMAGE=$newgreedy_image
WOS_V2_PUBLIC_HOST=v2-ci.example.invalid
WOS_V2_GRAFANA_HOST=monitoring-v2-ci.example.invalid
WOS_V2_ALLOWED_HOSTS=["v2-ci.example.invalid","api"]
WOS_V2_STORAGE_HOST_PATH=$smoke_root/data
WOS_V2_POSTGRES_DB=world_of_seeds_v2
WOS_V2_POSTGRES_USER=world_of_seeds_v2
WOS_V2_POSTGRES_PASSWORD=ci-only-database-password-not-used
WOS_V2_INTEGRATION_ACCOUNTS_JSON={"routes":[{"tracker_account_ref":"11111111-1111-4111-8111-111111111111","qbittorrent_account_ref":"22222222-2222-4222-8222-222222222222","newgreedy_url":"http://newgreedy:8080","c411_passkey":"ci-only-not-a-real-passkey","qbittorrent_url":"http://qbittorrent:8080","qbittorrent_username":"ci-only","qbittorrent_password":"ci-only-password"}]}
WOS_V2_QBITTORRENT_CONFIG_PATH=$qbittorrent_config
WOS_V2_NEWGREEDY_CONFIG_PATH=$config_file
WOS_V2_NEWGREEDY_STATE_HOST_PATH=$state_dir
WOS_V2_APP_UID=10001
WOS_V2_APP_GID=10001
WOS_V2_QBITTORRENT_UID=10001
WOS_V2_QBITTORRENT_GID=10002
WOS_V2_NEWGREEDY_GID=10003
WOS_V2_WORKER_REPLICAS=2
WOS_V2_GRAFANA_ADMIN_USER=ci-only
WOS_V2_GRAFANA_ADMIN_PASSWORD=ci-only-grafana-password
WOS_V2_PROMETHEUS_RETENTION=1d
EOF
sudo chmod 0600 "$environment"

sudo "$repository/scripts/rise2_v2_preflight.sh" "$environment"

compose up --detach --wait --wait-timeout 120 newgreedy
wait_healthy
compose exec -T newgreedy curl -fsS http://127.0.0.1:8080/api/health >/dev/null
newgreedy_container="$(compose ps --quiet newgreedy)"
sudo docker inspect "$newgreedy_container" \
    | jq -e '.[0].NetworkSettings.Ports["8080/tcp"] == null' >/dev/null \
    || fail "NewGreedy published a host port"
compose exec -T newgreedy test -w /app/stats.json
compose exec -T newgreedy test -w /app/torrent_registry.json
compose exec -T newgreedy test -w /app/newgreedy.log
compose exec -T newgreedy test -w /app/purge_pending.json
compose exec -T newgreedy test -s /root/.mitmproxy/mitmproxy-ca-cert.pem
compose exec -T newgreedy test -s /root/.mitmproxy/mitmproxy-ca.pem
ca_initial="$(compose exec -T newgreedy sha256sum /root/.mitmproxy/mitmproxy-ca-cert.pem | awk '{print $1}')"
[[ "$ca_initial" =~ ^[0-9a-f]{64}$ ]] || fail "initial CA fingerprint is invalid"

compose restart newgreedy
wait_healthy
compose exec -T newgreedy curl -fsS http://127.0.0.1:8080/api/health >/dev/null
ca_restart="$(compose exec -T newgreedy sha256sum /root/.mitmproxy/mitmproxy-ca-cert.pem | awk '{print $1}')"
[[ "$ca_restart" == "$ca_initial" ]] || fail "CA changed after restart"

compose stop newgreedy
sudo python3 - "$state_dir/stats.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.write_text(json.dumps({"_ci_sentinel": "preserved"}) + "\n", encoding="utf-8")
PY
compose up --detach --force-recreate --wait --wait-timeout 120 newgreedy
wait_healthy
compose exec -T newgreedy curl -fsS http://127.0.0.1:8080/api/health >/dev/null
compose exec -T newgreedy python3 -c \
    'import json; assert json.load(open("/app/stats.json", encoding="utf-8"))["_ci_sentinel"] == "preserved"'
ca_recreate="$(compose exec -T newgreedy sha256sum /root/.mitmproxy/mitmproxy-ca-cert.pem | awk '{print $1}')"
[[ "$ca_recreate" == "$ca_initial" ]] || fail "CA changed after container recreation"

echo "NewGreedy image: $newgreedy_image"
echo "CA fingerprint initial: $ca_initial"
echo "CA fingerprint after restart: $ca_restart"
echo "CA fingerprint after recreate: $ca_recreate"
echo "Persistent state sentinel: preserved"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    cat >>"$GITHUB_STEP_SUMMARY" <<EOF
## Rise2 NewGreedy runtime smoke

- Image: \`$newgreedy_image\`
- Health after start/restart/recreate: **passed**
- CA initial: \`$ca_initial\`
- CA after restart: \`$ca_restart\`
- CA after recreate: \`$ca_recreate\`
- Persistent state sentinel after recreate: **preserved**
- Published host ports: **none**
EOF
fi
