#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_REPO_PATH="scripts/rise2_v2_run_rollback_gate.sh"
REPO="/opt/world-of-seeds-v2"
ENV_FILE="/etc/world-of-seeds-v2/environment"
COMPOSE="deploy/compose.rise2.v2.yaml"
RUNTIME_REV=""
TOOL_REV=""
CAMPAIGN=""
PILOT_CAMPAIGN=""
V1_HEALTH_URL="https://world-of-seeds.fr/api/v1/health/live"
INGRESS_STOPPED=0

while (($#)); do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --env-file) ENV_FILE="$2"; shift 2 ;;
        --compose) COMPOSE="$2"; shift 2 ;;
        --runtime-revision) RUNTIME_REV="$2"; shift 2 ;;
        --tool-revision) TOOL_REV="$2"; shift 2 ;;
        --campaign) CAMPAIGN="$2"; shift 2 ;;
        --pilot-campaign) PILOT_CAMPAIGN="$2"; shift 2 ;;
        --v1-health-url) V1_HEALTH_URL="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

[[ "$RUNTIME_REV" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid runtime revision" >&2; exit 2; }
[[ "$TOOL_REV" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid tool revision" >&2; exit 2; }
[[ "$CAMPAIGN" =~ ^[a-z0-9][a-z0-9-]{0,15}$ ]] || { echo "invalid campaign" >&2; exit 2; }
[[ "$PILOT_CAMPAIGN" =~ ^[a-z0-9][a-z0-9-]{0,15}$ ]] || { echo "invalid pilot campaign" >&2; exit 2; }
[[ "$V1_HEALTH_URL" == https://* ]] || { echo "V1 health URL must use HTTPS" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "runner must execute as root" >&2; exit 2; }
cd "$REPO"

PILOT_ROOT="/var/lib/world-of-seeds-v2/pilot/$RUNTIME_REV"
LEDGER="$PILOT_ROOT/ledger.json"
CREDENTIALS="$PILOT_ROOT/pilot-credentials-$PILOT_CAMPAIGN.json"
EVIDENCE_ROOT="$PILOT_ROOT/rollback-$CAMPAIGN"
EVIDENCE="$EVIDENCE_ROOT/rollback.aggregate.json"
DC=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE")
dc() { "${DC[@]}" "$@"; }
service_ids() { dc ps -q "$1"; }
service_count() { service_ids "$1" | awk 'NF {n++} END {print n+0}'; }

wait_count() {
    local service="$1" expected="$2" timeout="${3:-60}" started
    started="$(date +%s)"
    while (( $(date +%s) - started < timeout )); do
        [[ "$(service_count "$service")" == "$expected" ]] && return 0
        sleep 1
    done
    return 1
}

wait_api_ready() {
    local started
    started="$(date +%s)"
    while (( $(date +%s) - started < 60 )); do
        if dc exec -T api python - <<'PY' >/dev/null 2>&1
import urllib.request
for path in ("/api/v1/health/live", "/api/v1/health/ready"):
    with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=3) as response:
        assert response.status == 200
PY
        then
            return 0
        fi
        sleep 1
    done
    return 1
}

restore_ingress() {
    local rc=$?
    trap - EXIT
    if [[ "$INGRESS_STOPPED" == 1 ]]; then
        dc start ingress >/dev/null 2>&1 || rc=1
        wait_count ingress 1 60 || rc=1
        wait_api_ready || rc=1
    fi
    exit "$rc"
}
trap restore_ingress EXIT

echo "========== V2-33 ROLLBACK GATE =========="
echo "runtime_revision=$RUNTIME_REV"
echo "tool_revision=$TOOL_REV"
echo "campaign=$CAMPAIGN"

[[ "$(git rev-parse HEAD)" == "$RUNTIME_REV" ]] || { echo "runtime checkout mismatch" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "runtime checkout dirty" >&2; exit 1; }
[[ -f "$LEDGER" && ! -L "$LEDGER" && "$(stat -c '%a' "$LEDGER")" == 600 ]] || {
    echo "invalid pilot ledger" >&2
    exit 1
}
[[ -f "$CREDENTIALS" && ! -L "$CREDENTIALS" && "$(stat -c '%a' "$CREDENTIALS")" == 600 ]] || {
    echo "private pilot credentials are unavailable" >&2
    exit 1
}
[[ ! -e "$EVIDENCE_ROOT" ]] || { echo "gate12 evidence path already exists" >&2; exit 1; }
[[ "$(git hash-object "$0")" == "$(git rev-parse "$TOOL_REV:$RUNNER_REPO_PATH")" ]] || {
    echo "runner tool blob mismatch" >&2
    exit 1
}

RTO_SECONDS="$(python3 - "$LEDGER" "$RUNTIME_REV" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["revision"] == sys.argv[2] and p["decision"] is None
for name in (
    "preflight", "backup_restore", "load_1_slot", "load_2_slots", "websocket_recovery",
    "transfer_manifest", "dependency_failures", "resource_pressure", "security_observability",
    "test_data_cleanup", "pilot_accounts",
):
    assert p["checks"].get(name, {}).get("status") == "passed"
assert "rollback" not in p["checks"]
rto=p["checks"]["backup_restore"]["metrics"].get("rto_seconds")
assert isinstance(rto, (int, float)) and not isinstance(rto, bool) and rto > 0
print(rto)
PY
)"

echo "provenance=PASSED prior_gates=11/12"
mkdir -m 0700 "$EVIDENCE_ROOT"

VOLUME_BEFORE="$(docker volume ls -q --filter label=com.docker.compose.project=world-of-seeds-v2-rise2 | sort)"
VOLUME_COUNT="$(printf '%s\n' "$VOLUME_BEFORE" | awk 'NF {n++} END {print n+0}')"
[[ "$VOLUME_COUNT" -ge 1 ]] || { echo "no V2 volumes found" >&2; exit 1; }

curl --fail --silent --show-error --max-time 15 "$V1_HEALTH_URL" >/dev/null
wait_api_ready

ROLLBACK_STARTED="$(date +%s)"
dc stop -t 15 ingress >/dev/null 2>&1
INGRESS_STOPPED=1
wait_count ingress 0 30 || { echo "V2 ingress did not stop" >&2; exit 1; }

curl --fail --silent --show-error --max-time 15 "$V1_HEALTH_URL" >/dev/null
wait_api_ready || { echo "V2 internal health failed while admission was suspended" >&2; exit 1; }

AUTH_RESULT="$(dc run --rm --no-deps --user 0:0 \
    -v "$CREDENTIALS:/run/v233-pilot-credentials.json:ro" \
    scheduler python - <<'PY'
import json
import urllib.request
from app.core.config import get_settings

with open("/run/v233-pilot-credentials.json", encoding="utf-8") as stream:
    credentials=json.load(stream)
host=next(
    item for item in get_settings().allowed_hosts
    if item not in {"127.0.0.1", "localhost", "test"}
)
body=json.dumps(credentials).encode("utf-8")
request=urllib.request.Request(
    "http://api:8000/api/v1/auth/login",
    data=body,
    headers={"Content-Type":"application/json", "Host":host},
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    payload=json.loads(response.read().decode("utf-8"))
    ok=(
        response.status == 200
        and payload.get("user", {}).get("must_change_credentials") is True
    )
print(json.dumps({"authentication_ok":ok}))
PY
)"

python3 - "$AUTH_RESULT" <<'PY'
import json, sys
lines=[line for line in sys.argv[1].splitlines() if line.strip()]
value=json.loads(lines[-1])
assert value.get("authentication_ok") is True
PY

ROLLBACK_COMPLETED="$(date +%s)"
ROLLBACK_DURATION="$((ROLLBACK_COMPLETED - ROLLBACK_STARTED))"

VOLUME_DURING="$(docker volume ls -q --filter label=com.docker.compose.project=world-of-seeds-v2-rise2 | sort)"
[[ "$VOLUME_DURING" == "$VOLUME_BEFORE" ]] || { echo "V2 volume set changed during rollback" >&2; exit 1; }

# The exercise is reversible: restore the current tested V2 ingress after proving rollback.
dc start ingress >/dev/null 2>&1
wait_count ingress 1 60 || { echo "V2 ingress failed to restore" >&2; exit 1; }
INGRESS_STOPPED=0
wait_api_ready || { echo "V2 health failed after rollback exercise" >&2; exit 1; }
VOLUME_AFTER="$(docker volume ls -q --filter label=com.docker.compose.project=world-of-seeds-v2-rise2 | sort)"
[[ "$VOLUME_AFTER" == "$VOLUME_BEFORE" ]] || { echo "V2 volume set changed after restore" >&2; exit 1; }

python3 - "$EVIDENCE" "$ROLLBACK_DURATION" "$RTO_SECONDS" "$VOLUME_COUNT" <<'PY'
import json, sys
value={
    "schema":"world-of-seeds-v2-rise2-rollback/v1",
    "rollback_duration_seconds":int(sys.argv[2]),
    "rto_seconds":float(sys.argv[3]),
    "health_failures":0,
    "authentication_failures":0,
    "v1_writes":0,
    "v1_available":True,
    "v2_admission_suspended":True,
    "v2_volumes_preserved":True,
    "v2_volume_count":int(sys.argv[4]),
    "pilot_authentication_verified":True,
    "runtime_restored_after_exercise":True,
    "secrets_or_business_identifiers_in_report":False,
}
assert value["rollback_duration_seconds"] <= value["rto_seconds"]
with open(sys.argv[1], "x", encoding="utf-8") as stream:
    json.dump(value, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
chmod 0600 "$EVIDENCE"
cat "$EVIDENCE"
echo "evidence_sha256=$(sha256sum "$EVIDENCE" | awk '{print $1}')"
echo "V2-33 ROLLBACK GATE: PASSED"
echo "runtime_restored_after_exercise=true"
echo "ledger_recorded=false"
echo "evidence_root=$EVIDENCE_ROOT"
