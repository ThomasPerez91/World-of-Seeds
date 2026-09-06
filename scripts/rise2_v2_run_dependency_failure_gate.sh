#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="world-of-seeds-v2-rise2"
RUNNER_REPO_PATH="scripts/rise2_v2_run_dependency_failure_gate.sh"
PROBE_REPO_PATH="scripts/rise2_v2_dependency_failure_probe.py"
REPO="/opt/world-of-seeds-v2"
ENV_FILE="/etc/world-of-seeds-v2/environment"
COMPOSE="deploy/compose.rise2.v2.yaml"
RUNTIME_REV=""
TOOL_REV=""
CAMPAIGN=""
SETUP_DONE=0

while (($#)); do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --env-file) ENV_FILE="$2"; shift 2 ;;
        --compose) COMPOSE="$2"; shift 2 ;;
        --runtime-revision) RUNTIME_REV="$2"; shift 2 ;;
        --tool-revision) TOOL_REV="$2"; shift 2 ;;
        --campaign) CAMPAIGN="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

[[ "$RUNTIME_REV" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid runtime revision" >&2; exit 2; }
[[ "$TOOL_REV" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid tool revision" >&2; exit 2; }
[[ "$CAMPAIGN" =~ ^[a-z0-9][a-z0-9-]{0,15}$ ]] || { echo "invalid campaign" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "runner must execute as root" >&2; exit 2; }
cd "$REPO"

PROBE="$(dirname "$(readlink -f "$0")")/rise2_v2_dependency_failure_probe.py"
PILOT_ROOT="/var/lib/world-of-seeds-v2/pilot/$RUNTIME_REV"
LEDGER="$PILOT_ROOT/ledger.json"
EVIDENCE_ROOT="$PILOT_ROOT/dependency-failures-$CAMPAIGN"
EVIDENCE="$EVIDENCE_ROOT/dependency_failures.aggregate.json"

DC=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE")
dc() { "${DC[@]}" "$@"; }
service_ids() { dc ps -q "$1"; }
service_all_ids() { dc ps --all -q "$1"; }
service_count() { service_ids "$1" | awk 'NF {n++} END {print n+0}'; }
inspect() { docker inspect -f "$2" "$1"; }

assert_project() {
    local cid="$1" service="$2" project actual
    project="$(inspect "$cid" '{{ index .Config.Labels "com.docker.compose.project" }}')"
    actual="$(inspect "$cid" '{{ index .Config.Labels "com.docker.compose.service" }}')"
    [[ "$project" == "$PROJECT" && "$actual" == "$service" ]] || {
        echo "refusing to touch a container outside the Rise2 V2 project" >&2
        return 1
    }
}

wait_count() {
    local service="$1" expected="$2" timeout="${3:-60}" start
    start="$(date +%s)"
    while (( $(date +%s) - start < timeout )); do
        [[ "$(service_count "$service")" == "$expected" ]] && return 0
        sleep 1
    done
    echo "$service running count did not reach $expected" >&2
    return 1
}

wait_health() {
    local service="$1" timeout="${2:-90}" start cid status
    start="$(date +%s)"
    while (( $(date +%s) - start < timeout )); do
        cid="$(service_ids "$service" | head -n1)"
        if [[ -n "$cid" ]]; then
            status="$(inspect "$cid" '{{if .State.Health}}{{.State.Health.Status}}{{end}}')"
            [[ "$status" == "healthy" ]] && return 0
        fi
        sleep 1
    done
    echo "$service did not become healthy" >&2
    return 1
}

stop_service() {
    dc stop -t 15 "$1" >/dev/null 2>&1
    wait_count "$1" 0 30
}

start_service() {
    dc start "$1" >/dev/null 2>&1
    case "$1" in
        worker) wait_count worker 2 60 ;;
        *) wait_count "$1" 1 60 ;;
    esac
}

api_observe() {
    local path="$1" timeout="${2:-3}" out
    out="$(dc exec -T api python -c '
import json,sys,urllib.error,urllib.request
p=sys.argv[1]; t=float(sys.argv[2])
try:
    r=urllib.request.urlopen("http://127.0.0.1:8000"+p, timeout=t)
    d=json.loads(r.read().decode())
    print(r.status, d.get("status", ""))
except urllib.error.HTTPError as e:
    print(e.code, "http_error")
except Exception:
    print(0, "unavailable")
' "$path" "$timeout" 2>/dev/null || true)"
    printf '%s\n' "${out:-0 unavailable}"
}

wait_api_status() {
    local expected="$1" timeout="${2:-45}" start observed
    start="$(date +%s)"
    while (( $(date +%s) - start < timeout )); do
        observed="$(api_observe /api/v1/health/status 3)"
        [[ "$observed" == "200 $expected" ]] && return 0
        sleep 1
    done
    echo "API system status did not become $expected" >&2
    return 1
}

wait_api_ready() {
    local timeout="${1:-45}" start
    start="$(date +%s)"
    while (( $(date +%s) - start < timeout )); do
        [[ "$(api_observe /api/v1/health/ready 3)" == "200 ok" ]] && return 0
        sleep 1
    done
    echo "API readiness did not recover" >&2
    return 1
}

integration_summary() {
    local service="$1" sql
    [[ "$service" == "qbittorrent" || "$service" == "newgreedy" ]] || return 2
    sql="SELECT count(*),count(*) FILTER (WHERE state='HEALTHY'),count(*) FILTER (WHERE state='UNAVAILABLE'),count(*) FILTER (WHERE valid_until < now()) FROM integration_service_health WHERE service='$service';"
    dc exec -T postgres sh -ec 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "|" -c "$1"' sh "$sql" 2>/dev/null
}

wait_integration() {
    local service="$1" expected="$2" timeout="${3:-60}" start raw total healthy unavailable stale
    start="$(date +%s)"
    while (( $(date +%s) - start < timeout )); do
        raw="$(integration_summary "$service")"
        IFS='|' read -r total healthy unavailable stale <<<"$raw"
        if [[ "$total" =~ ^[0-9]+$ && "$total" -gt 0 ]]; then
            if [[ "$expected" == "HEALTHY" && "$healthy" == "$total" && "$unavailable" == 0 && "$stale" == 0 ]]; then
                return 0
            fi
            if [[ "$expected" == "UNAVAILABLE" && "$unavailable" == "$total" && "$healthy" == 0 ]]; then
                return 0
            fi
        fi
        sleep 2
    done
    echo "$service did not become $expected" >&2
    return 1
}

probe_mode() {
    local mode="$1" out
    out="$(dc run --rm --no-deps \
        -v "$PROBE:/bootstrap/rise2_v2_dependency_failure_probe.py:ro" \
        scheduler python /bootstrap/rise2_v2_dependency_failure_probe.py \
        "$mode" --campaign "$CAMPAIGN" 2>/dev/null)"
    printf '%s\n' "$out" | awk 'NF {line=$0} END {print line}'
}

verify_snapshot_json() {
    local json="$1" require_backoff="${2:-false}"
    python3 - "$json" "$require_backoff" <<'PY'
import json, sys
s=json.loads(sys.argv[1]); backoff=sys.argv[2] == "true"
assert s.get("sentinel_torrents") == 9 and s.get("sentinel_jobs") == 9
assert s.get("queued_jobs") == 9 and s.get("running_jobs") == 0
assert s.get("future_sync_jobs") == 8
assert s.get("recovery_canary_queued") is True
assert s.get("recovery_canary_unclaimed") is True
assert s.get("recovery_canary_error_safe") is True
assert s.get("recovery_canary_attempt_count") == 1
if backoff:
    assert s.get("recovery_canary_backoff") is True
PY
}

verify_sentinels() { verify_snapshot_json "$(probe_mode snapshot)" false; }

wait_canary() {
    local start snap
    start="$(date +%s)"
    while (( $(date +%s) - start < 45 )); do
        snap="$(probe_mode snapshot)"
        if verify_snapshot_json "$snap" true 2>/dev/null; then
            return 0
        fi
        sleep 2
    done
    echo "expired job recovery/backoff was not observed" >&2
    return 1
}

unpause_postgres() {
    local ids cid
    ids="$(service_all_ids postgres)"
    [[ "$(printf '%s\n' "$ids" | awk 'NF {n++} END {print n+0}')" == 1 ]] || return 1
    cid="$(printf '%s\n' "$ids" | head -n1)"
    assert_project "$cid" postgres
    if [[ "$(inspect "$cid" '{{.State.Paused}}')" == true ]]; then
        docker unpause "$cid" >/dev/null
    fi
}

recover_runtime() {
    unpause_postgres
    local service
    for service in postgres redis newgreedy qbittorrent api worker scheduler ingress; do
        dc start "$service" >/dev/null 2>&1 || return 1
    done
    wait_count postgres 1 60
    wait_count redis 1 60
    wait_count newgreedy 1 60
    wait_count qbittorrent 1 60
    wait_count api 1 60
    wait_count worker 2 60
    wait_count scheduler 1 60
    wait_count ingress 1 60
    for service in api postgres redis qbittorrent newgreedy; do wait_health "$service" 90; done
    wait_api_ready 45
    wait_api_status ok 45
    wait_integration qbittorrent HEALTHY 60
    wait_integration newgreedy HEALTHY 60
}

emergency_cleanup() {
    local rc=$?
    trap - EXIT
    recover_runtime || { echo "RECOVERY RUNTIME ERROR" >&2; rc=1; }
    if [[ "$SETUP_DONE" == 1 ]]; then
        probe_mode cleanup >/dev/null || { echo "RECOVERY CLEANUP ERROR" >&2; rc=1; }
    fi
    exit "$rc"
}
trap emergency_cleanup EXIT

echo "========== V2-33 DEPENDENCY FAILURES GATE =========="
echo "runtime_revision=$RUNTIME_REV"
echo "tool_revision=$TOOL_REV"
echo "campaign=$CAMPAIGN"

[[ "$(git rev-parse HEAD)" == "$RUNTIME_REV" ]] || { echo "runtime checkout mismatch" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "runtime checkout dirty" >&2; exit 1; }
[[ -f "$LEDGER" && ! -L "$LEDGER" && "$(stat -c '%a' "$LEDGER")" == 600 ]] || { echo "invalid pilot ledger" >&2; exit 1; }
[[ ! -e "$EVIDENCE_ROOT" ]] || { echo "gate7 evidence path already exists" >&2; exit 1; }
[[ -f "$PROBE" && ! -L "$PROBE" ]] || { echo "probe missing or symlinked" >&2; exit 1; }
[[ "$(git hash-object "$0")" == "$(git rev-parse "$TOOL_REV:$RUNNER_REPO_PATH")" ]] || { echo "runner tool blob mismatch" >&2; exit 1; }
[[ "$(git hash-object "$PROBE")" == "$(git rev-parse "$TOOL_REV:$PROBE_REPO_PATH")" ]] || { echo "probe tool blob mismatch" >&2; exit 1; }

IMAGE_DIGEST="$(python3 - "$LEDGER" "$RUNTIME_REV" <<'PY'
import json,sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["revision"] == sys.argv[2] and p["decision"] is None
for name in ("preflight","backup_restore","load_1_slot","load_2_slots","websocket_recovery","transfer_manifest"):
    assert p["checks"].get(name,{}).get("status") == "passed"
assert "dependency_failures" not in p["checks"]
print(p["image_digest"])
PY
)"

for service in api worker scheduler postgres redis qbittorrent newgreedy ingress; do
    expected=1; [[ "$service" == worker ]] && expected=2
    [[ "$(service_count "$service")" == "$expected" ]] || { echo "unexpected running count for $service" >&2; exit 1; }
    while read -r cid; do [[ -z "$cid" ]] || assert_project "$cid" "$service"; done < <(service_ids "$service")
done
for service in api worker scheduler; do
    while read -r cid; do
        [[ -z "$cid" ]] && continue
        [[ "$(inspect "$cid" '{{.Config.Image}}')" == *"@$IMAGE_DIGEST" ]] || { echo "application image provenance mismatch" >&2; exit 1; }
        [[ "$(inspect "$cid" '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" == "$RUNTIME_REV" ]] || { echo "application control-plane provenance mismatch" >&2; exit 1; }
    done < <(service_ids "$service")
done
for service in api postgres redis qbittorrent newgreedy; do wait_health "$service" 30; done
wait_api_status ok 20
wait_integration qbittorrent HEALTHY 30
wait_integration newgreedy HEALTHY 30
echo "provenance=PASSED prior_gates=6/12"

mkdir -m 0700 "$EVIDENCE_ROOT"
setup="$(probe_mode setup)"
python3 - "$setup" <<'PY'
import json,sys
s=json.loads(sys.argv[1]); assert s.get("sentinel_jobs") == 9 and s.get("sentinel_torrents") == 9
PY
SETUP_DONE=1
wait_canary
echo "expired_job_recovery=PASSED backoff=PASSED"

echo "scenario=1/8 redis_unavailable"
stop_service redis
wait_api_status degraded 15
[[ "$(api_observe /api/v1/health/live 3)" == "200 ok" ]] || { echo "API liveness lost during Redis outage" >&2; exit 1; }
start_service redis
wait_health redis 45
wait_api_status ok 30
verify_sentinels

echo "scenario=2/8 postgres_stall"
PG_CID="$(service_ids postgres | head -n1)"
assert_project "$PG_CID" postgres
docker pause "$PG_CID" >/dev/null
sleep 1
[[ "$(api_observe /api/v1/health/ready 2)" != "200 ok" ]] || { echo "PostgreSQL stall produced false readiness" >&2; exit 1; }
unpause_postgres
wait_health postgres 45
wait_api_ready 45
verify_sentinels

echo "scenario=3/8 qbittorrent_unavailable"
stop_service qbittorrent
wait_integration qbittorrent UNAVAILABLE 45
start_service qbittorrent
wait_health qbittorrent 90
wait_integration qbittorrent HEALTHY 60
verify_sentinels

echo "scenario=4/8 newgreedy_unavailable"
stop_service newgreedy
wait_integration newgreedy UNAVAILABLE 45
start_service newgreedy
wait_health newgreedy 90
wait_integration newgreedy HEALTHY 60
verify_sentinels

echo "scenario=5/8 worker_outage"
stop_service worker
verify_sentinels
start_service worker
verify_sentinels

echo "scenario=6/8 scheduler_outage"
stop_service scheduler
verify_sentinels
start_service scheduler
wait_integration qbittorrent HEALTHY 60
wait_integration newgreedy HEALTHY 60
verify_sentinels

echo "scenario=7/8 qbittorrent_reset"
dc restart -t 15 qbittorrent >/dev/null 2>&1
wait_count qbittorrent 1 60
wait_health qbittorrent 90
wait_integration qbittorrent HEALTHY 60
verify_sentinels

echo "scenario=8/8 ingress_api_outage"
stop_service ingress
stop_service api
[[ "$(service_count ingress)" == 0 && "$(service_count api)" == 0 ]] || { echo "ingress/API outage not observed" >&2; exit 1; }
verify_sentinels
start_service api
wait_health api 90
wait_api_ready 45
start_service ingress
verify_sentinels

recover_runtime
recover_runtime
verify_sentinels
cleanup="$(probe_mode cleanup)"
python3 - "$cleanup" <<'PY'
import json,sys
s=json.loads(sys.argv[1]); assert s.get("remaining_torrents") == 0 and s.get("remaining_jobs") == 0
PY
SETUP_DONE=0
trap - EXIT

umask 077
python3 - "$EVIDENCE" <<'PY'
import json,sys
value={
 "schema":"world-of-seeds-v2-rise2-dependency-failures/v1",
 "scenarios":8,
 "scenario_passes":8,
 "false_successes":0,
 "lost_jobs":0,
 "recovery_failures":0,
 "idempotent_recovery":True,
 "backoff_verified":True,
 "durable_queue_preserved":True,
 "redis_degraded_observed":True,
 "postgres_stall_observed":True,
 "qbittorrent_unavailable_observed":True,
 "newgreedy_unavailable_observed":True,
 "worker_recovery_observed":True,
 "scheduler_recovery_observed":True,
 "qbittorrent_reset_observed":True,
 "ingress_api_recovery_observed":True,
 "secrets_or_business_identifiers_in_report":False,
}
with open(sys.argv[1], "x", encoding="utf-8") as f:
    json.dump(value,f,indent=2,sort_keys=True); f.write("\n")
PY
chmod 0600 "$EVIDENCE"
cat "$EVIDENCE"
echo "evidence_sha256=$(sha256sum "$EVIDENCE" | awk '{print $1}')"
echo "V2-33 DEPENDENCY FAILURES GATE: PASSED"
echo "ledger_recorded=false"
echo "evidence_root=$EVIDENCE_ROOT"
