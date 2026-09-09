#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_REPO_PATH="scripts/rise2_v2_run_pilot_accounts_gate.sh"
PROBE_REPO_PATH="scripts/rise2_v2_data_hygiene_probe.py"
REPO="/opt/world-of-seeds-v2"
ENV_FILE="/etc/world-of-seeds-v2/environment"
COMPOSE="deploy/compose.rise2.v2.yaml"
RUNTIME_REV=""
TOOL_REV=""
CAMPAIGN=""

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

PROBE="$(dirname "$(readlink -f "$0")")/rise2_v2_data_hygiene_probe.py"
PILOT_ROOT="/var/lib/world-of-seeds-v2/pilot/$RUNTIME_REV"
LEDGER="$PILOT_ROOT/ledger.json"
EVIDENCE_ROOT="$PILOT_ROOT/pilot-accounts-$CAMPAIGN"
EVIDENCE="$EVIDENCE_ROOT/pilot_accounts.aggregate.json"
CREDENTIALS="$PILOT_ROOT/pilot-credentials-$CAMPAIGN.json"
DC=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE")
dc() { "${DC[@]}" "$@"; }
service_ids() { dc ps -q "$1"; }

echo "========== V2-33 PILOT ACCOUNTS GATE =========="
echo "runtime_revision=$RUNTIME_REV"
echo "tool_revision=$TOOL_REV"
echo "campaign=$CAMPAIGN"

[[ "$(git rev-parse HEAD)" == "$RUNTIME_REV" ]] || { echo "runtime checkout mismatch" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "runtime checkout dirty" >&2; exit 1; }
[[ -f "$LEDGER" && ! -L "$LEDGER" && "$(stat -c '%a' "$LEDGER")" == 600 ]] || {
    echo "invalid pilot ledger" >&2
    exit 1
}
[[ ! -e "$EVIDENCE_ROOT" ]] || { echo "gate11 evidence path already exists" >&2; exit 1; }
[[ ! -e "$CREDENTIALS" ]] || { echo "pilot credential destination already exists" >&2; exit 1; }
[[ -f "$PROBE" && ! -L "$PROBE" ]] || { echo "probe missing or symlinked" >&2; exit 1; }
[[ "$(git hash-object "$0")" == "$(git rev-parse "$TOOL_REV:$RUNNER_REPO_PATH")" ]] || {
    echo "runner tool blob mismatch" >&2
    exit 1
}
[[ "$(git hash-object "$PROBE")" == "$(git rev-parse "$TOOL_REV:$PROBE_REPO_PATH")" ]] || {
    echo "probe tool blob mismatch" >&2
    exit 1
}

python3 - "$LEDGER" "$RUNTIME_REV" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["revision"] == sys.argv[2] and p["decision"] is None
for name in (
    "preflight", "backup_restore", "load_1_slot", "load_2_slots",
    "websocket_recovery", "transfer_manifest", "dependency_failures", "resource_pressure",
    "security_observability", "test_data_cleanup",
):
    assert p["checks"].get(name, {}).get("status") == "passed"
assert "pilot_accounts" not in p["checks"]
PY

API_CID="$(service_ids api | head -n1)"
[[ -n "$API_CID" ]] || { echo "API container unavailable" >&2; exit 1; }
DATA_HOST="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$API_CID")"
[[ -n "$DATA_HOST" && -d "$DATA_HOST" ]] || { echo "unable to resolve isolated V2 data root" >&2; exit 1; }
TEMP_CREDENTIALS="$DATA_HOST/.v233-pilot-credentials-$CAMPAIGN.json"
[[ ! -e "$TEMP_CREDENTIALS" ]] || { echo "pilot credential staging file already exists" >&2; exit 1; }

echo "provenance=PASSED prior_gates=10/12"
mkdir -m 0700 "$EVIDENCE_ROOT"

RESULT="$(dc run --rm --no-deps \
    -v "$PROBE:/bootstrap/rise2_v2_data_hygiene_probe.py:ro" \
    scheduler python /bootstrap/rise2_v2_data_hygiene_probe.py \
    pilot-create --campaign "$CAMPAIGN" 2>/dev/null | awk 'NF {line=$0} END {print line}')"

[[ -f "$TEMP_CREDENTIALS" && ! -L "$TEMP_CREDENTIALS" ]] || {
    echo "pilot credential staging file missing" >&2
    exit 1
}
[[ "$(stat -c '%a' "$TEMP_CREDENTIALS")" == 600 ]] || {
    echo "pilot credential staging file mode is unsafe" >&2
    exit 1
}
[[ "$(stat -c '%s' "$TEMP_CREDENTIALS")" -gt 0 && "$(stat -c '%s' "$TEMP_CREDENTIALS")" -lt 4096 ]] || {
    echo "pilot credential staging file size is invalid" >&2
    exit 1
}

python3 - "$TEMP_CREDENTIALS" "$CAMPAIGN" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value=json.load(stream)
assert set(value) == {"username", "password"}
assert value["username"] == f"pilot-{sys.argv[2]}"
assert isinstance(value["password"], str) and len(value["password"]) >= 12
PY

install -o root -g root -m 0600 -- "$TEMP_CREDENTIALS" "$CREDENTIALS"
rm -f -- "$TEMP_CREDENTIALS"

python3 - "$RESULT" "$EVIDENCE" <<'PY'
import json, sys
result=json.loads(sys.argv[1])
value={
    "schema":"world-of-seeds-v2-rise2-pilot-accounts/v1",
    **result,
    "credentials_saved_private":True,
    "secrets_or_business_identifiers_in_report":False,
}
assert value["pilot_account_count"] >= 1
assert value["forced_credential_change"] is True
assert value["pilot_account_active"] is True
assert value["account_ready"] is True
assert value["credential_file_written"] is True
assert value["v1_data_moves"] == 0
assert value["credentials_in_output"] == 0
assert value["v1_write_operations"] == 0
assert value["v1_unchanged"] is True
with open(sys.argv[2], "x", encoding="utf-8") as stream:
    json.dump(value, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
chmod 0600 "$EVIDENCE"
cat "$EVIDENCE"
echo "evidence_sha256=$(sha256sum "$EVIDENCE" | awk '{print $1}')"
echo "V2-33 PILOT ACCOUNTS GATE: PASSED"
echo "pilot_credentials_saved=true"
echo "ledger_recorded=false"
echo "evidence_root=$EVIDENCE_ROOT"