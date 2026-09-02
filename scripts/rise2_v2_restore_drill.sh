#!/bin/sh
set -eu

usage() {
  echo "Usage: $0 <verified-restore-directory> <content-snapshot-id> <report.json>" >&2
  exit 2
}

[ "$#" -eq 3 ] || usage

restore_dir=$1
snapshot_id=$2
report_path=$3

[ -d "$restore_dir" ] || { echo "Restore directory does not exist" >&2; exit 1; }
[ -n "$snapshot_id" ] || { echo "Content snapshot ID is required" >&2; exit 1; }
case "$snapshot_id" in
  *[!A-Za-z0-9._:-]*) echo "Content snapshot ID contains unsafe characters" >&2; exit 1 ;;
esac
[ ! -e "$report_path" ] || { echo "Report path already exists" >&2; exit 1; }
[ -d "$(dirname "$report_path")" ] || { echo "Report parent does not exist" >&2; exit 1; }

for required in \
  "$restore_dir/manifest.json" \
  "$restore_dir/environment" \
  "$restore_dir/postgres.dump" \
  "$restore_dir/qBittorrent.conf" \
  "$restore_dir/newgreedy/config.ini"
do
  [ -f "$required" ] || { echo "Restore payload is incomplete" >&2; exit 1; }
done
[ -d "$restore_dir/qbittorrent-config" ] || { echo "qBittorrent state is missing" >&2; exit 1; }
[ -d "$restore_dir/newgreedy-state" ] || { echo "NewGreedy state is missing" >&2; exit 1; }
[ -d "$restore_dir/newgreedy-ca" ] || { echo "NewGreedy CA is missing" >&2; exit 1; }
for required in stats.json torrent_registry.json newgreedy.log purge_pending.json; do
  [ -f "$restore_dir/newgreedy-state/$required" ] || {
    echo "NewGreedy state is incomplete" >&2
    exit 1
  }
done
for required in mitmproxy-ca-cert.pem mitmproxy-ca.pem; do
  [ -f "$restore_dir/newgreedy-ca/$required" ] || {
    echo "NewGreedy CA is incomplete" >&2
    exit 1
  }
done

python3 - "$restore_dir/manifest.json" "$snapshot_id" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    manifest = json.load(stream)
content = manifest.get("content", {})
if content.get("snapshot_id") != sys.argv[2]:
    raise SystemExit("Content snapshot ID does not match the verified payload")
if content.get("policy") != "external-snapshot-required":
    raise SystemExit("Restore payload has no external snapshot policy")
PY

[ "$(stat -c '%a' "$restore_dir/environment")" = 600 ] || {
  echo "Restored environment mode must be 0600" >&2
  exit 1
}
[ "$(stat -c '%a' "$restore_dir/qBittorrent.conf")" = 600 ] || {
  echo "Restored qBittorrent config mode must be 0600" >&2
  exit 1
}
[ "$(stat -c '%a' "$restore_dir/newgreedy/config.ini")" = 640 ] || {
  echo "Restored NewGreedy config mode must be 0640" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "openssl is required" >&2; exit 1; }

drill_suffix="$(date -u +%Y%m%d%H%M%S)-$(openssl rand -hex 8)"
drill_container="wos-v2-restore-drill-$drill_suffix"
drill_volume="wos-v2-restore-drill-$drill_suffix"
drill_password=$(openssl rand -hex 32)
started_at=$(date +%s)
volume_created=false
container_created=false

cleanup() {
  if [ "$container_created" = true ]; then
    docker rm --force "$drill_container" >/dev/null 2>&1 || true
  fi
  if [ "$volume_created" = true ]; then
    volume_owner=$(docker volume inspect \
      --format '{{ index .Labels "org.worldofseeds.restore-drill" }}' \
      "$drill_volume" 2>/dev/null || true)
    if [ "$volume_owner" = "$drill_suffix" ]; then
      docker volume rm "$drill_volume" >/dev/null 2>&1 || true
    fi
  fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

if docker container inspect "$drill_container" >/dev/null 2>&1; then
  echo "Disposable restore container name already exists" >&2
  exit 1
fi
if docker volume inspect "$drill_volume" >/dev/null 2>&1; then
  echo "Disposable restore volume name already exists" >&2
  exit 1
fi
docker volume create \
  --label "org.worldofseeds.restore-drill=$drill_suffix" \
  "$drill_volume" >/dev/null
volume_created=true
docker run --detach \
  --name "$drill_container" \
  --network none \
  --security-opt no-new-privileges:true \
  --volume "$drill_volume:/var/lib/postgresql/data" \
  --env "POSTGRES_PASSWORD=$drill_password" \
  --env POSTGRES_USER=wos_restore \
  --env POSTGRES_DB=wos_restore \
  postgres:17.11-alpine3.24 >/dev/null
container_created=true

attempt=0
until docker exec "$drill_container" pg_isready \
  --username wos_restore --dbname wos_restore >/dev/null 2>&1
do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 60 ] || { echo "Disposable PostgreSQL did not become ready" >&2; exit 1; }
  sleep 1
done

if ! docker exec --interactive "$drill_container" pg_restore \
  --username wos_restore \
  --dbname wos_restore \
  --no-owner \
  --no-privileges < "$restore_dir/postgres.dump" >/dev/null 2>&1
then
  echo "PostgreSQL restore drill failed" >&2
  exit 1
fi

table_count=$(docker exec "$drill_container" psql \
  --username wos_restore \
  --dbname wos_restore \
  --tuples-only \
  --no-align \
  --command "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'" 2>/dev/null)
case "$table_count" in
  ''|*[!0-9]*) echo "PostgreSQL restore validation returned an invalid result" >&2; exit 1 ;;
  0) echo "PostgreSQL restore contains no public table" >&2; exit 1 ;;
esac

qb_file_count=$(find "$restore_dir/qbittorrent-config" -type f -print | wc -l | tr -d ' ')
newgreedy_file_count=$(find "$restore_dir/newgreedy-state" -type f -print | wc -l | tr -d ' ')
newgreedy_ca_file_count=$(find "$restore_dir/newgreedy-ca" -type f -print | wc -l | tr -d ' ')
finished_at=$(date +%s)
duration_seconds=$((finished_at - started_at))

umask 077
python3 - "$report_path" "$snapshot_id" "$duration_seconds" "$table_count" \
  "$qb_file_count" "$newgreedy_file_count" "$newgreedy_ca_file_count" <<'PY'
import json
import sys
from datetime import UTC, datetime

report = {
    "schema": 1,
    "result": "pass",
    "completed_at": datetime.now(UTC).isoformat(),
    "content_snapshot_id": sys.argv[2],
    "duration_seconds": int(sys.argv[3]),
    "postgres_public_table_count": int(sys.argv[4]),
    "qbittorrent_state_file_count": int(sys.argv[5]),
    "newgreedy_state_file_count": int(sys.argv[6]),
    "newgreedy_ca_file_count": int(sys.argv[7]),
    "secrets_included": False,
    "filenames_included": False,
}
with open(sys.argv[1], "x", encoding="utf-8") as stream:
    json.dump(report, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY

echo "Isolated Rise2 V2 restore drill passed; report: $report_path"
