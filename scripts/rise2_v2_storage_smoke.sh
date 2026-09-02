#!/bin/sh
set -eu

repository=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
environment=${1:-/etc/world-of-seeds-v2/environment}
compose_file="$repository/deploy/compose.rise2.v2.yaml"

fail() {
  echo "Rise2 V2 storage smoke failed: $1" >&2
  exit 1
}

env_value() {
  key=$1
  value=$(sed -n "s/^${key}=//p" "$environment" | tail -n 1)
  [ -n "$value" ] || fail "missing $key"
  printf '%s\n' "$value"
}

storage=$(env_value WOS_V2_STORAGE_HOST_PATH)
app_uid=$(env_value WOS_V2_APP_UID)
app_gid=$(env_value WOS_V2_APP_GID)
qbittorrent_uid=$(env_value WOS_V2_QBITTORRENT_UID)
qbittorrent_gid=$(env_value WOS_V2_QBITTORRENT_GID)

[ "$qbittorrent_uid" = "$app_uid" ] \
  || fail "qBittorrent UID must equal the WOS application UID"

case "$storage" in
  /srv/world-of-seeds-v2/*) ;;
  *) fail "storage must be inside /srv/world-of-seeds-v2" ;;
esac
[ -d "$storage" ] || fail "storage directory not found"
[ ! -L "$storage" ] || fail "storage directory must not be a symlink"

probe="rise2preflight$$"
probe_root="$storage/$probe"

cleanup() {
  case "$probe_root" in
    "$storage"/rise2preflight[0-9]*) rm -rf -- "$probe_root" ;;
    *) : ;;
  esac
}
trap cleanup EXIT INT TERM

[ ! -e "$probe_root" ] || fail "temporary probe path already exists"

compose() {
  docker compose --env-file "$environment" -f "$compose_file" "$@"
}

compose run --rm --no-deps \
  --env WOS_PREFLIGHT_USER="$probe" \
  --entrypoint python \
  api -c '
import os
import stat
from pathlib import Path
from app.files.workspaces import WorkspaceManager

username = os.environ["WOS_PREFLIGHT_USER"]
manager = WorkspaceManager(Path("/data"))
manager.create(username)
manager.assert_ready(username)
for path in (Path("/data") / username, Path("/data") / username / "downloads"):
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o750:
        raise SystemExit(f"unexpected workspace mode: {mode:o}")
'

compose run --rm --no-deps \
  --user "$qbittorrent_uid:$qbittorrent_gid" \
  --env WOS_PREFLIGHT_USER="$probe" \
  --entrypoint /bin/sh \
  qbittorrent -ec '
file="/data/$WOS_PREFLIGHT_USER/downloads/qb-created.bin"
printf "qB storage probe\n" >"$file"
test -s "$file"
'

compose run --rm --no-deps \
  --env WOS_PREFLIGHT_USER="$probe" \
  --entrypoint python \
  api -c '
import os
from pathlib import Path
from app.files.workspaces import WorkspaceManager

username = os.environ["WOS_PREFLIGHT_USER"]
manager = WorkspaceManager(Path("/data"))
manager.assert_ready(username)
downloads = Path("/data") / username / "downloads"
source = downloads / "qb-created.bin"
destination = downloads / "wos-renamed.bin"
source.rename(destination)
destination.unlink()
manager.remove_empty(username)
'

[ ! -e "$probe_root" ] || fail "WOS did not remove the temporary workspace"
trap - EXIT INT TERM

echo "Rise2 V2 shared-storage smoke passed."
