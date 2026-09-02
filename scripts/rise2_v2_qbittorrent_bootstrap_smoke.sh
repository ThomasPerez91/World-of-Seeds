#!/bin/sh
set -eu

repository=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
workdir=$(mktemp -d)
container="wos-rise2-qb-bootstrap-smoke-$$"
password="rise2-ci-qb-password"

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -rf "$workdir"
}
trap cleanup EXIT INT TERM

fail_with_logs() {
  echo "qBittorrent bootstrap smoke failed: $1" >&2
  docker logs "$container" 2>&1 \
    | grep -Ei 'WebUI|WebAPI|login|Host header|Origin header|Referer header' >&2 \
    || true
  exit 1
}

mkdir -p "$workdir/config/qBittorrent/config"

python3 - "$password" <<'PY' \
  | python3 "$repository/scripts/rise2_v2_qbittorrent_bootstrap.py" \
      --output "$workdir/config/qBittorrent/config/qBittorrent.conf" \
      --uid "$(id -u)" \
      --gid "$(id -g)"
import json
import sys

password = sys.argv[1]
registry = {
    "routes": [
        {
            "tracker_account_ref": "11111111-1111-4111-8111-111111111111",
            "qbittorrent_account_ref": "22222222-2222-4222-8222-222222222222",
            "newgreedy_url": "http://newgreedy:8080",
            "c411_passkey": "ci-only-placeholder",
            "qbittorrent_url": "http://qbittorrent:8080",
            "qbittorrent_username": "wos-v2",
            "qbittorrent_password": password,
        }
    ]
}
print(
    json.dumps(
        {
            "services": {
                "scheduler": {
                    "environment": {
                        "WOS_INTEGRATION_ACCOUNTS_JSON": json.dumps(
                            registry, separators=(",", ":")
                        )
                    }
                }
            }
        }
    )
)
PY

config="$workdir/config/qBittorrent/config/qBittorrent.conf"
test "$(stat -c '%a' "$config")" = "600"
! grep -F "$password" "$config" >/dev/null
! grep -F 'ci-only-placeholder' "$config" >/dev/null

uid=$(id -u)
gid=$(id -g)

docker run --detach \
  --name "$container" \
  --hostname qbittorrent \
  --publish 127.0.0.1:0:8080 \
  --env "PUID=$uid" \
  --env "PGID=$gid" \
  --env QBT_LEGAL_NOTICE=confirm \
  --env QBT_WEBUI_PORT=8080 \
  --env UMASK=077 \
  --volume "$workdir/config:/config" \
  qbittorrentofficial/qbittorrent-nox:5.2.3-1 >/dev/null

ready=false
for _ in $(seq 1 30); do
  if docker exec "$container" \
    wget -q -O /dev/null http://127.0.0.1:8080/api/v2/app/version 2>/dev/null; then
    ready=true
    break
  fi
  sleep 1
done
[ "$ready" = true ] || fail_with_logs "WebUI did not become ready"

# Localhost bypass is deliberately retained for the container healthcheck. Use it
# to prove that qBittorrent 5.2.3 actually parsed the generated settings before
# testing the authenticated Docker-service hostname path.
docker exec "$container" \
  curl -fsS \
    -H 'Origin: http://127.0.0.1:8080' \
    -H 'Referer: http://127.0.0.1:8080/' \
    http://127.0.0.1:8080/api/v2/app/preferences \
  >"$workdir/preferences.json" \
  || fail_with_logs "could not read local preferences"

python3 - "$workdir/preferences.json" <<'PY' || fail_with_logs "generated preferences differ"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    preferences = json.load(handle)

expected = {
    "save_path": "/data",
    "web_ui_domain_list": "qbittorrent",
    "web_ui_host_header_validation_enabled": True,
    "web_ui_csrf_protection_enabled": True,
    "bypass_local_auth": True,
    "proxy_type": "HTTP",
    "proxy_ip": "newgreedy",
    "proxy_port": 3456,
    "proxy_bittorrent": True,
    "proxy_peer_connections": False,
    "proxy_rss": False,
    "proxy_misc": False,
}
for key, value in expected.items():
    actual = preferences.get(key)
    if actual != value:
        raise SystemExit(f"unexpected qBittorrent preference {key}: {actual!r}")
PY

published=$(docker port "$container" 8080/tcp | head -n 1)
port=${published##*:}
[ -n "$port" ] || fail_with_logs "published WebUI port is missing"
base="http://127.0.0.1:$port"
cookie="$workdir/cookie"

status=$(curl --silent --show-error \
  --output "$workdir/login-body" \
  --write-out '%{http_code}' \
  --cookie-jar "$cookie" \
  --header 'Host: qbittorrent:8080' \
  --header 'Origin: http://qbittorrent:8080' \
  --header 'Referer: http://qbittorrent:8080/' \
  --data-urlencode 'username=wos-v2' \
  --data-urlencode "password=$password" \
  "$base/api/v2/auth/login") \
  || fail_with_logs "login request failed"

if [ "$status" != "200" ] || [ "$(cat "$workdir/login-body")" != "Ok." ]; then
  echo "qBittorrent bootstrap smoke login HTTP=$status body=$(cat "$workdir/login-body")" >&2
  fail_with_logs "authenticated Docker-service login was rejected"
fi

curl --fail --silent --show-error \
  --cookie "$cookie" \
  --header 'Host: qbittorrent:8080' \
  --header 'Origin: http://qbittorrent:8080' \
  --header 'Referer: http://qbittorrent:8080/' \
  "$base/api/v2/torrents/info?limit=1&offset=0&sort=hash&reverse=false" \
  >"$workdir/inventory.json" \
  || fail_with_logs "authenticated inventory request failed"

python3 - "$workdir/inventory.json" <<'PY' || fail_with_logs "inventory response is invalid"
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
if not isinstance(payload, list) or len(payload) > 1:
    raise SystemExit("unexpected bounded qBittorrent inventory response")
PY

echo "Rise2 qBittorrent bootstrap smoke passed."
