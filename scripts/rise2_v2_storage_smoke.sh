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
wos_image=$(env_value WOS_V2_IMAGE)
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
printf '%s\n' "$wos_image" | grep -Eq '^.+@sha256:[0-9a-f]{64}$' \
  || fail "WOS image must use an immutable sha256 digest"

probe=$(printf '%032x' "$$")
probe_root="$storage/content/$probe"

cleanup() {
  case "$probe_root" in
    "$storage"/content/[0-9a-f][0-9a-f]*) rm -rf -- "$probe_root" ;;
    *) : ;;
  esac
}
trap cleanup EXIT INT TERM

[ ! -e "$probe_root" ] || fail "temporary probe path already exists"

compose() {
  docker compose --env-file "$environment" -f "$compose_file" "$@"
}

# The live API owns a fixed edge-network address. A compose one-off for the api
# service inherits that address and collides with the running container. Storage
# probes need no network, so run the exact immutable WOS image in network-none
# isolation instead of joining the production Compose networks.
docker pull "$wos_image" >/dev/null

run_wos_probe() {
  docker run --rm --pull=never \
    --network none \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
    --user "$app_uid:$app_gid" \
    --mount "type=bind,src=$storage,dst=/data" \
    --env WOS_PREFLIGHT_STORAGE_KEY="$probe" \
    --entrypoint python \
    "$wos_image" -c "$1"
}

run_wos_probe '
import os
import stat
from pathlib import Path
from uuid import UUID
from app.storage import SharedContentStore

key = UUID(hex=os.environ["WOS_PREFLIGHT_STORAGE_KEY"])
store = SharedContentStore(Path("/data"))
store.prepare(key)
path = Path("/data") / "content" / key.hex
mode = stat.S_IMODE(path.stat().st_mode)
if mode != 0o750:
    raise SystemExit(f"unexpected managed content mode: {mode:o}")
with store.open_directory(key):
    pass
'

compose run --rm --no-deps \
  --user "$qbittorrent_uid:$qbittorrent_gid" \
  --env WOS_PREFLIGHT_STORAGE_KEY="$probe" \
  --entrypoint /bin/sh \
  qbittorrent -ec '
file="/data/content/$WOS_PREFLIGHT_STORAGE_KEY/qb-created.bin"
printf "qB storage probe\n" >"$file"
test -s "$file"
'

run_wos_probe '
import os
import stat
from pathlib import Path
from uuid import UUID
from app.storage import SharedContentStore

key = UUID(hex=os.environ["WOS_PREFLIGHT_STORAGE_KEY"])
store = SharedContentStore(Path("/data"))
with store.open_directory(key) as directory_fd:
    metadata = os.stat("qb-created.bin", dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
        raise SystemExit("qB storage probe file is invalid")
    os.rename(
        "qb-created.bin",
        "wos-renamed.bin",
        src_dir_fd=directory_fd,
        dst_dir_fd=directory_fd,
    )
    os.unlink("wos-renamed.bin", dir_fd=directory_fd)
store.remove_empty(key)
'

[ ! -e "$probe_root" ] || fail "WOS did not remove the temporary managed content directory"
trap - EXIT INT TERM

echo "Rise2 V2 shared-storage smoke passed."
