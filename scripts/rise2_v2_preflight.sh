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
allowed_hosts=$(env_value WOS_V2_ALLOWED_HOSTS)
newgreedy_config=$(env_value WOS_V2_NEWGREEDY_CONFIG_PATH)
newgreedy_state=$(env_value WOS_V2_NEWGREEDY_STATE_HOST_PATH)
newgreedy_image=$(env_value WOS_V2_NEWGREEDY_IMAGE)
qbittorrent_config=$(env_value WOS_V2_QBITTORRENT_CONFIG_PATH)
app_uid=$(env_value WOS_V2_APP_UID)
app_gid=$(env_value WOS_V2_APP_GID)
newgreedy_gid=$(env_value WOS_V2_NEWGREEDY_GID)
qbittorrent_uid=$(env_value WOS_V2_QBITTORRENT_UID)
qbittorrent_gid=$(env_value WOS_V2_QBITTORRENT_GID)

case "$allowed_hosts" in
  *'"127.0.0.1"'*) ;;
  *) fail "WOS_V2_ALLOWED_HOSTS must include 127.0.0.1 for the local API healthcheck" ;;
esac

case "$storage" in
  /srv/world-of-seeds-v2/*) ;;
  *) fail "storage must be inside /srv/world-of-seeds-v2" ;;
esac
[ -d "$storage" ] || fail "storage directory not found"
[ ! -L "$storage" ] || fail "storage directory must not be a symlink"
mountpoint -q -- "$storage" || fail "storage directory must be an active mountpoint"
[ "$qbittorrent_uid" = "$app_uid" ] \
  || fail "qBittorrent UID must equal the WOS application UID for shared 0750 workspaces"
[ "$(stat -c '%u' "$storage")" = "$app_uid" ] \
  || fail "storage root owner must match the shared WOS/qBittorrent UID"
[ "$(stat -c '%g' "$storage")" = "$app_gid" ] \
  || fail "storage root group must match the WOS application GID"
[ "$(stat -c '%a' "$storage")" = "750" ] \
  || fail "storage root mode must be 0750"

printf '%s\n' "$newgreedy_image" | grep -Eq '^.+@sha256:[0-9a-f]{64}$' \
  || fail "NewGreedy image must use an immutable sha256 digest"

[ -f "$newgreedy_config" ] || fail "NewGreedy config not found"
[ ! -L "$newgreedy_config" ] || fail "NewGreedy config must not be a symlink"
[ "$(stat -c '%a' "$newgreedy_config")" = "640" ] || fail "NewGreedy config mode must be 0640"
[ "$(stat -c '%u' "$newgreedy_config")" = "$app_uid" ] \
  || fail "NewGreedy config owner must be the WOS application UID"
[ "$(stat -c '%g' "$newgreedy_config")" = "$newgreedy_gid" ] \
  || fail "NewGreedy config group must match the container GID"

case "$newgreedy_state" in
  /srv/world-of-seeds-v2/*) ;;
  *) fail "NewGreedy state must be inside /srv/world-of-seeds-v2" ;;
esac
[ -d "$newgreedy_state" ] || fail "NewGreedy state directory not found"
[ ! -L "$newgreedy_state" ] || fail "NewGreedy state directory must not be a symlink"
[ "$(stat -c '%a' "$newgreedy_state")" = "700" ] \
  || fail "NewGreedy state directory mode must be 0700"
[ "$(stat -c '%u:%g' "$newgreedy_state")" = "0:0" ] \
  || fail "NewGreedy state directory must be owned by root"

# Derive the private bootstrap from the same registry used by WOS. Never source
# the environment as shell code or print its JSON/credentials.
python3 "$repository/scripts/rise2_v2_qb_bootstrap.py" "$environment"
python3 "$repository/scripts/rise2_v2_qb_bootstrap.py" "$environment" --check
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

# Normalize the production name for policy only. Runtime commands still honor a
# caller's isolated COMPOSE_PROJECT_NAME (used by disposable acceptance tests).
compose --project-name world-of-seeds-v2-rise2 config --format json \
  | python3 "$repository/scripts/validate_compose_v2_rise2.py"
sh "$repository/scripts/rise2_v2_storage_smoke.sh" "$environment"
compose run --rm --no-deps newgreedy-init

for name in stats.json torrent_registry.json newgreedy.log purge_pending.json; do
  state_file="$newgreedy_state/$name"
  [ -f "$state_file" ] || fail "NewGreedy state file is missing: $name"
  [ ! -L "$state_file" ] || fail "NewGreedy state file must not be a symlink: $name"
  [ "$(stat -c '%a' "$state_file")" = "600" ] \
    || fail "NewGreedy state file mode must be 0600: $name"
  [ "$(stat -c '%u:%g' "$state_file")" = "0:0" ] \
    || fail "NewGreedy state file must be owned by root: $name"
done

compose run --rm --no-deps --entrypoint /bin/sh \
  --env WOS_EXPECTED_GID="$newgreedy_gid" \
  newgreedy -ec \
  'test "$(id -u)" = "0"
   case " $(id -G) " in *" $WOS_EXPECTED_GID "*) ;; *) exit 1 ;; esac
   test -r /app/config.ini
   test -w /app/stats.json
   test -w /app/torrent_registry.json
   test -w /app/newgreedy.log
   test -w /app/purge_pending.json
   test -w /root/.mitmproxy'

echo "Rise2 V2 preflight passed."
