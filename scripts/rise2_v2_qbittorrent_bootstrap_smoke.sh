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
  --publish 127.0.0.1::8080 \
  --add-host newgreedy:127.0.0.1 \
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
[ "$ready" = true ] || {
  docker logs "$container" >&2
  echo "qBittorrent bootstrap smoke: WebUI did not become ready" >&2
  exit 1
}

published=$(docker port "$container" 8080/tcp | head -n 1)
port=${published##*:}
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
  "$base/api/v2/auth/login")

[ "$status" = "200" ]
[ "$(cat "$workdir/login-body")" = "Ok." ]

curl --fail --silent --show-error \
  --cookie "$cookie" \
  --header 'Host: qbittorrent:8080' \
  --header 'Origin: http://qbittorrent:8080' \
  --header 'Referer: http://qbittorrent:8080/' \
  "$base/api/v2/app/preferences" \
  >"$workdir/preferences.json"

python3 - "$workdir/preferences.json" <<'PY'
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

echo "Rise2 qBittorrent bootstrap smoke passed."
