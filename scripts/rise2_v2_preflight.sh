#!/bin/sh
set -eu

repository=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
environment=${1:-/etc/world-of-seeds-v2/environment}
compose_file="$repository/deploy/compose.rise2.v2.yaml"

fail() {
  echo "Rise2 V2 preflight failed: $1" >&2
  exit 1
}

env_value() {
  key=$1
  value=$(sed -n "s/^${key}=//p" "$environment" | tail -n 1)
  [ -n "$value" ] || fail "missing $key"
  printf '%s\n' "$value"
}

[ -f "$environment" ] || fail "environment file not found"
[ ! -L "$environment" ] || fail "environment file must not be a symlink"
[ "$(stat -c '%a' "$environment")" = "600" ] || fail "environment file mode must be 0600"

storage=$(env_value WOS_V2_STORAGE_HOST_PATH)
newgreedy_config=$(env_value WOS_V2_NEWGREEDY_CONFIG_PATH)
qbittorrent_config=$(env_value WOS_V2_QBITTORRENT_CONFIG_PATH)
app_uid=$(env_value WOS_V2_APP_UID)
newgreedy_gid=$(env_value WOS_V2_NEWGREEDY_GID)
qbittorrent_uid=$(env_value WOS_V2_QBITTORRENT_UID)
qbittorrent_gid=$(env_value WOS_V2_QBITTORRENT_GID)

case "$storage" in
  /srv/world-of-seeds-v2/*) ;;
  *) fail "storage must be inside /srv/world-of-seeds-v2" ;;
esac
[ -d "$storage" ] || fail "storage directory not found"
[ ! -L "$storage" ] || fail "storage directory must not be a symlink"

[ -f "$newgreedy_config" ] || fail "NewGreedy config not found"
[ ! -L "$newgreedy_config" ] || fail "NewGreedy config must not be a symlink"
[ "$(stat -c '%a' "$newgreedy_config")" = "640" ] || fail "NewGreedy config mode must be 0640"
[ "$(stat -c '%u' "$newgreedy_config")" = "$app_uid" ] \
  || fail "NewGreedy config owner must be the WOS application UID"
[ "$(stat -c '%g' "$newgreedy_config")" = "$newgreedy_gid" ] \
  || fail "NewGreedy config group must match the container GID"

[ -f "$qbittorrent_config" ] || fail "qBittorrent bootstrap config not found"
[ ! -L "$qbittorrent_config" ] || fail "qBittorrent config must not be a symlink"
[ "$(stat -c '%a' "$qbittorrent_config")" = "600" ] \
  || fail "qBittorrent config mode must be 0600"
[ "$(stat -c '%u' "$qbittorrent_config")" = "$qbittorrent_uid" ] \
  || fail "qBittorrent config owner must match its container UID"
[ "$(stat -c '%g' "$qbittorrent_config")" = "$qbittorrent_gid" ] \
  || fail "qBittorrent config group must match its container GID"

compose() {
  docker compose --env-file "$environment" -f "$compose_file" "$@"
}

compose config --format json | python3 "$repository/scripts/validate_compose_v2_rise2.py"
compose run --rm --no-deps --entrypoint /bin/sh \
  --env WOS_EXPECTED_UID="$(env_value WOS_V2_NEWGREEDY_UID)" \
  --env WOS_EXPECTED_GID="$newgreedy_gid" \
  newgreedy -ec \
  'test "$(id -u)" = "$WOS_EXPECTED_UID" && test "$(id -g)" = "$WOS_EXPECTED_GID" && test -r /app/config.ini'

echo "Rise2 V2 preflight passed."
