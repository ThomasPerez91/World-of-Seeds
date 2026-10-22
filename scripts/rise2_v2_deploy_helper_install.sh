#!/usr/bin/env bash
# Operator bootstrap only. CI's forced SSH command cannot install root helpers.
set -Eeuo pipefail
umask 077
readonly PROJECT_DIR=/opt/world-of-seeds-v2
readonly TARGET=/usr/local/sbin/deploy-world-of-seeds-v2-rise2
readonly STATE_DIR=/var/lib/world-of-seeds-v2/deploy
stage=""
cleanup() { [[ -z "$stage" ]] || rm -f -- "$stage"; }
trap cleanup EXIT
[[ ${EUID} -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ $# == 2 && "$1" =~ ^[0-9a-f]{40}$ && "$2" =~ ^[0-9a-f]{64}$ ]] || {
  echo 'Usage: rise2_v2_deploy_helper_install.sh <reviewed-commit> <expected-helper-sha256>' >&2
  exit 1
}
revision="$1"
expected="$2"
for tool in git bash sha256sum flock pgrep stat mktemp; do
  command -v "$tool" >/dev/null
done
[[ -d "$PROJECT_DIR/.git" && ! -L "$PROJECT_DIR" ]] || exit 1
[[ ! -L "$TARGET" && -d /usr/local/sbin && ! -L /usr/local/sbin ]] || exit 1
[[ $(stat -c '%U:%G' /usr/local/sbin) == root:root ]] || exit 1
directory_mode=$(stat -c '%a' /usr/local/sbin)
(( (8#$directory_mode & 0022) == 0 )) || exit 1
install -d -m 700 -o root -g root "$STATE_DIR"
exec {LOCK_FD}>"$STATE_DIR/deployment.lock"
flock --nonblock "$LOCK_FD" || { echo 'A deployment is running.' >&2; exit 1; }
if pgrep -f '/usr/local/sbin/deploy-world-of-seeds-v2-rise2[[:space:]]' >/dev/null; then
  echo 'A legacy deployment is running; wait for it to finish.' >&2
  exit 1
fi
# Read an exact immutable object; do not check out code or execute its preflight.
git -c safe.directory="$PROJECT_DIR" -C "$PROJECT_DIR" cat-file -e "$revision^{commit}"
stage=$(mktemp /usr/local/sbin/.wos-v2-deploy.XXXXXX)
git -c safe.directory="$PROJECT_DIR" -C "$PROJECT_DIR" show "$revision:deploy/deploy-world-of-seeds-v2-rise2" >"$stage"
printf '%s  %s\n' "$expected" "$stage" | sha256sum --check --status
bash -n "$stage"
chown root:root "$stage"
chmod 0755 "$stage"
mv -fT -- "$stage" "$TARGET"
stage=""
printf 'Helper installed from reviewed commit %s\n' "$revision"
sha256sum "$TARGET"
