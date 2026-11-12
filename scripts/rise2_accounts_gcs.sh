#!/usr/bin/env bash
# Run as a dedicated service user with a read-only PostgreSQL login.
set -euo pipefail
umask 077

: "${WOS_BACKUP_RECIPIENT:?set the public age recipient}"
: "${WOS_DATABASE_URL:?set the read-only PostgreSQL URL}"
: "${WOS_BACKUP_WORKDIR:?set a private directory owned by the service user}"
: "${WOS_BACKUP_PYTHON:?set the backend virtualenv Python}"
bucket="${WOS_BACKUP_BUCKET:-gs://world_of_seeds_bucket}"

exec 9>"$WOS_BACKUP_WORKDIR/export.lock"
flock -n 9
staging=$(mktemp -d "$WOS_BACKUP_WORKDIR/upload.XXXXXXXX")
trap 'rm -f -- "$archive" "$staging/success"; rmdir -- "$staging"' EXIT
archive="$staging/accounts-$(date -u +%Y%m%dT%H%M%SZ)-${staging##*.}.json.age"

"$WOS_BACKUP_PYTHON" -m app.account_backup export --recipient "$WOS_BACKUP_RECIPIENT" >"$archive"
test -s "$archive"
gcloud storage cp --if-generation-match=0 "$archive" "$bucket/wos-accounts/v1/"
date -u +%s >"$staging/success"
mv -f -- "$staging/success" "$WOS_BACKUP_WORKDIR/last-success"
