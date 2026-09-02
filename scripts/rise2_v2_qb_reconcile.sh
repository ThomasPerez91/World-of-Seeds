#!/bin/sh
set -eu
umask 077

fail() { echo "qB bootstrap failed: $1" >&2; exit 1; }
bootstrap=${QBT_BOOTSTRAP_DIR:-/bootstrap}
root=${QBT_CONFIG_ROOT:-/config}
uid=${QBT_UID:-${PUID:?qB UID required}}
gid=${QBT_GID:-${PGID:?qB GID required}}
case "$uid:$gid" in *[!0-9:]*|:*|*:) fail "invalid identity" ;; esac
[ "$uid" -gt 0 ] && [ "$gid" -gt 0 ] || fail "non-root qB identity required"
for path in "$root" "$root/qBittorrent" "$root/qBittorrent/config"; do
    [ ! -L "$path" ] || fail "profile directory is a symlink"
    if [ ! -e "$path" ]; then
        install -d -o "$uid" -g "$gid" -m 0700 "$path"
    fi
    [ -d "$path" ] || fail "invalid profile directory"
done
config="$root/qBittorrent/config/qBittorrent.conf"
[ ! -L "$config" ] || fail "profile config is a symlink"
for file in policy.conf qBittorrent.conf reconcile.awk; do
    [ -f "$bootstrap/$file" ] && [ ! -L "$bootstrap/$file" ] && [ -s "$bootstrap/$file" ] \
        || fail "missing bootstrap input; run preflight"
done
# Compose can rerun the init dependency while an existing qB is still running.
# Never edit a live profile from another container: the runtime reconciles it
# before starting qB. First-install init still prepares the complete final file.
if [ "${1:-}" = "--init" ] && [ -f "$config" ]; then
    echo "Existing qB profile preserved; runtime will reconcile before qB starts."
    exit 0
fi
temporary=$(mktemp "$root/qBittorrent/config/.wos-qb-XXXXXX")
seed="$temporary.seed"
trap 'rm -f "$temporary" "$seed"' EXIT HUP INT TERM
source="$config"
if [ ! -e "$source" ]; then
    cp "$bootstrap/qBittorrent.conf" "$seed"
    source="$seed"
fi
[ -f "$source" ] || fail "profile config is not a regular file"
awk -f "$bootstrap/reconcile.awk" "$bootstrap/policy.conf" "$bootstrap/qBittorrent.conf" "$source" >"$temporary" \
    || fail "profile reconciliation rejected"
chmod 0600 "$temporary"
chown "$uid:$gid" "$temporary"
if [ -f "$config" ] && cmp -s "$temporary" "$config" \
    && [ "$(stat -c '%a:%u:%g' "$config")" = "600:$uid:$gid" ]; then
    : # No replacement or metadata syscall on an unchanged profile.
else
    mv -f "$temporary" "$config"
fi
echo "qB authentication, Host/CSRF and tracker-only proxy contract reconciled."
