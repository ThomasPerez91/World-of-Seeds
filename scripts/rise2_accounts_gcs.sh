#!/usr/bin/env bash
# Daily encrypted export of WoS accounts to Google Cloud Storage.
# Runs on Rise2 as root through world-of-seeds-v2-accounts-backup.service, like the PostgreSQL
# backup: the snapshot is produced inside the running api container (the only place that reaches
# PostgreSQL on the internal network) and encrypted with age on the host, inside the pipe.
# Plaintext never touches the disk.
set -euo pipefail
umask 077

readonly PROJECT=world-of-seeds-v2-rise2
readonly ENV_FILE="${WOS_V2_ENV_FILE:-/etc/world-of-seeds-v2/environment}"
readonly REPOSITORY="${WOS_V2_REPOSITORY:-/opt/world-of-seeds-v2}"
readonly RECIPIENT_FILE="${WOS_BACKUP_RECIPIENT_FILE:-/etc/world-of-seeds-v2/accounts-backup-recipient}"
readonly WORKDIR="${WOS_BACKUP_WORKDIR:-/var/lib/world-of-seeds-v2/accounts-backup}"
readonly METRICS="${WOS_BACKUP_METRICS:-/var/lib/world-of-seeds-v2/node-exporter-textfile/wos_accounts_backup.prom}"
readonly BUCKET="${WOS_BACKUP_BUCKET:-gs://world_of_seeds_bucket}"

fail() {
  echo "accounts backup: $*" >&2
  exit 1
}

archive=""
staging=""
success=0

publish_metrics() {
  local last_success=0 temporary
  if [[ -f "$WORKDIR/last-success" ]]; then
    last_success="$(<"$WORKDIR/last-success")"
    [[ "$last_success" =~ ^[0-9]+$ ]] || last_success=0
  fi
  [[ -d "$(dirname -- "$METRICS")" ]] || return 0
  temporary="$(mktemp "$METRICS.XXXXXXXX")" || return 0
  printf 'wos_accounts_backup_last_success_timestamp_seconds %s\nwos_accounts_backup_last_attempt_success %s\n' \
    "$last_success" "$success" >"$temporary"
  chmod 0644 -- "$temporary"
  mv -f -- "$temporary" "$METRICS"
}

cleanup() {
  [[ -z "$archive" ]] || rm -f -- "$archive"
  [[ -z "$staging" ]] || rmdir -- "$staging" 2>/dev/null || true
  publish_metrics || true
}

[[ "$(id -u)" == 0 ]] || fail "run as root (Docker access), through the systemd unit"
command -v flock >/dev/null || fail "missing prerequisite: flock"
[[ ! -L "$WORKDIR" ]] || fail "work directory must not be a symlink"
install -d -m 0700 -- "$WORKDIR"
exec 9>"$WORKDIR/.lock"
flock -n 9 || fail "another account backup is running"
# From here on, every exit refreshes the metrics, so a broken setup alerts like a failed upload.
trap cleanup EXIT

for tool in docker age gcloud; do
  command -v "$tool" >/dev/null || fail "missing prerequisite: $tool"
done
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail "Rise2 environment file is missing"
[[ -f "$REPOSITORY/deploy/compose.rise2.v2.yaml" ]] || fail "Rise2 checkout is missing"
[[ -f "$RECIPIENT_FILE" && ! -L "$RECIPIENT_FILE" ]] || fail "age recipient file is missing"
recipient="$(<"$RECIPIENT_FILE")"
[[ "$recipient" =~ ^age1[0-9a-z]{58}$ ]] || fail "age recipient file must hold one public recipient"
[[ "$BUCKET" =~ ^gs://[a-z0-9][a-z0-9._-]{1,220}$ ]] || fail "invalid bucket"

staging="$(mktemp -d "$WORKDIR/upload.XXXXXXXX")"
archive="$staging/accounts-$(date -u +%Y%m%dT%H%M%SZ)-${staging##*.}.json.age"

docker compose \
  --project-name "$PROJECT" \
  --env-file "$ENV_FILE" \
  --file "$REPOSITORY/deploy/compose.rise2.v2.yaml" \
  exec -T api python -m app.account_backup dump \
  | age --recipient "$recipient" --output "$archive"
[[ -s "$archive" ]] || fail "empty encrypted archive"

gcloud storage cp --quiet --if-generation-match=0 "$archive" "$BUCKET/wos-accounts/v1/"

date -u +%s >"$WORKDIR/last-success.tmp"
mv -f -- "$WORKDIR/last-success.tmp" "$WORKDIR/last-success"
success=1
echo "accounts backup: uploaded $(basename -- "$archive")"
