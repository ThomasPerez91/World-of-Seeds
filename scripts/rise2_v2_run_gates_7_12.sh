#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_REPO_PATH="scripts/rise2_v2_run_gates_7_12.sh"
REPO="/opt/world-of-seeds-v2"
RUNTIME_REV=""
TOOL_REV=""
V1_HEALTH_URL="https://world-of-seeds.fr/api/v1/health/live"

while (($#)); do
    case "$1" in
        --repo) REPO="$2"; shift 2 ;;
        --runtime-revision) RUNTIME_REV="$2"; shift 2 ;;
        --tool-revision) TOOL_REV="$2"; shift 2 ;;
        --v1-health-url) V1_HEALTH_URL="$2"; shift 2 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

[[ "$RUNTIME_REV" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid runtime revision" >&2; exit 2; }
[[ "$TOOL_REV" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid tool revision" >&2; exit 2; }
[[ "$V1_HEALTH_URL" == https://* ]] || { echo "V1 health URL must use HTTPS" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "orchestrator must execute as root" >&2; exit 2; }
cd "$REPO"

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
PILOT="$SCRIPT_DIR/rise2_v2_pilot.py"
PILOT_ROOT="/var/lib/world-of-seeds-v2/pilot/$RUNTIME_REV"
LEDGER="$PILOT_ROOT/ledger.json"
CURRENT_GATE="precheck"
RUN_ID="$(date -u +%m%d%H%M%S)"

TOOLS=(
    rise2_v2_pilot.py
    rise2_v2_run_dependency_failure_gate.sh
    rise2_v2_dependency_failure_probe.py
    rise2_v2_run_resource_pressure_gate.sh
    rise2_v2_resource_pressure_probe.py
    rise2_v2_run_security_observability_gate.sh
    rise2_v2_run_test_data_cleanup_gate.sh
    rise2_v2_data_hygiene_probe.py
    rise2_v2_run_pilot_accounts_gate.sh
    rise2_v2_run_rollback_gate.sh
    rise2_v2_run_gates_7_12.sh
)

ledger_summary() {
    python3 - "$LEDGER" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    ledger=json.load(stream)
order=(
    "preflight","backup_restore","load_1_slot","load_2_slots","websocket_recovery",
    "transfer_manifest","dependency_failures","resource_pressure","security_observability",
    "test_data_cleanup","pilot_accounts","rollback",
)
print("========== PILOT LEDGER SUMMARY ==========")
for index, name in enumerate(order, 1):
    entry=ledger.get("checks", {}).get(name)
    if entry is None:
        print(f"{index:02d} {name}: PENDING")
        continue
    metrics=entry.get("metrics", {})
    selected={
        key: metrics[key]
        for key in sorted(metrics)
        if key in {
            "scenarios","false_successes","lost_jobs","recovery_failures",
            "admission_failures","unexplained_threshold_breaches",
            "fixable_high_critical","secret_findings","business_identifier_findings",
            "remaining_test_users","remaining_test_torrents","remaining_test_files",
            "pilot_account_count","health_failures","authentication_failures","v1_writes",
            "rto_seconds",
        }
    }
    suffix=(" metrics=" + json.dumps(selected, sort_keys=True)) if selected else ""
    print(
        f"{index:02d} {name}: {entry['status'].upper()} "
        f"duration={entry['duration_seconds']}s{suffix}"
    )
print(f"decision={ledger.get('decision')}")
PY
}

failure_report() {
    local rc=$?
    trap - ERR EXIT
    if [[ $rc -ne 0 ]]; then
        echo
        echo "========================================"
        echo "V2-33 GATES 7-12: FAILED"
        echo "failed_gate=$CURRENT_GATE"
        echo "tool_revision=$TOOL_REV"
        echo "========================================"
        ledger_summary || true
    fi
    exit "$rc"
}
trap failure_report ERR EXIT

is_passed() {
    python3 - "$LEDGER" "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    ledger=json.load(stream)
entry=ledger.get("checks", {}).get(sys.argv[2])
raise SystemExit(0 if entry is not None and entry.get("status") == "passed" else 1)
PY
}

assert_unrecorded() {
    python3 - "$LEDGER" "$1" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    ledger=json.load(stream)
if sys.argv[2] in ledger.get("checks", {}):
    raise SystemExit(f"gate {sys.argv[2]} is already recorded but not passed")
PY
}

record_gate() {
    local name="$1" duration="$2" evidence="$3"
    shift 3
    local -a metric_args=()
    local metric
    for metric in "$@"; do
        metric_args+=(--metric "$metric")
    done
    python3 "$PILOT" record "$LEDGER" "$name" \
        --status passed \
        --duration-seconds "$duration" \
        --evidence "$evidence" \
        "${metric_args[@]}"
    python3 "$PILOT" validate "$LEDGER"
    echo "recorded_gate=$name duration_seconds=$duration"
}

run_standard_gate() {
    local name="$1" runner="$2" campaign="$3" evidence="$4"
    shift 4
    if is_passed "$name"; then
        echo "========== SKIP $name: ALREADY PASSED =========="
        return 0
    fi
    assert_unrecorded "$name"
    CURRENT_GATE="$name"
    echo
    echo "========== START $name =========="
    local started ended duration
    started="$(date +%s)"
    /bin/bash "$SCRIPT_DIR/$runner" \
        --runtime-revision "$RUNTIME_REV" \
        --tool-revision "$TOOL_REV" \
        --campaign "$campaign"
    ended="$(date +%s)"
    duration="$((ended - started))"
    [[ -s "$evidence" && ! -L "$evidence" ]] || {
        echo "missing evidence for $name" >&2
        return 1
    }
    record_gate "$name" "$duration" "$evidence" "$@"
}

echo "========== V2-33 CONSOLIDATED GATES 7-12 =========="
echo "runtime_revision=$RUNTIME_REV"
echo "tool_revision=$TOOL_REV"
echo "run_id=$RUN_ID"

[[ "$(git rev-parse HEAD)" == "$RUNTIME_REV" ]] || { echo "runtime checkout mismatch" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "runtime checkout dirty" >&2; exit 1; }
[[ -f "$LEDGER" && ! -L "$LEDGER" && "$(stat -c '%a' "$LEDGER")" == 600 ]] || {
    echo "invalid pilot ledger" >&2
    exit 1
}
[[ -f "$PILOT" && ! -L "$PILOT" ]] || { echo "pilot tool missing" >&2; exit 1; }
[[ "$(git hash-object "$0")" == "$(git rev-parse "$TOOL_REV:$RUNNER_REPO_PATH")" ]] || {
    echo "orchestrator blob mismatch" >&2
    exit 1
}
for name in "${TOOLS[@]}"; do
    path="$SCRIPT_DIR/$name"
    [[ -f "$path" && ! -L "$path" ]] || { echo "tool missing: $name" >&2; exit 1; }
    [[ "$(git hash-object "$path")" == "$(git rev-parse "$TOOL_REV:scripts/$name")" ]] || {
        echo "tool blob mismatch: $name" >&2
        exit 1
    }
done

python3 "$PILOT" validate "$LEDGER"
python3 - "$LEDGER" "$RUNTIME_REV" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    ledger=json.load(stream)
assert ledger["revision"] == sys.argv[2]
assert ledger["decision"] is None
for name in (
    "preflight","backup_restore","load_1_slot","load_2_slots",
    "websocket_recovery","transfer_manifest",
):
    assert ledger["checks"].get(name, {}).get("status") == "passed"
for name, entry in ledger["checks"].items():
    if name in {
        "dependency_failures","resource_pressure","security_observability",
        "test_data_cleanup","pilot_accounts","rollback",
    } and entry.get("status") != "passed":
        raise SystemExit(f"existing non-passing gate prevents resume: {name}")
PY

echo "provenance=PASSED prior_gates_at_least=6/12"
ledger_summary

D7_CAMPAIGN="df-$RUN_ID"
D8_CAMPAIGN="rp-$RUN_ID"
D9_CAMPAIGN="so-$RUN_ID"
D10_CAMPAIGN="cl-$RUN_ID"
D11_CAMPAIGN="pa-$RUN_ID"
D12_CAMPAIGN="rb-$RUN_ID"

run_standard_gate \
    dependency_failures \
    rise2_v2_run_dependency_failure_gate.sh \
    "$D7_CAMPAIGN" \
    "$PILOT_ROOT/dependency-failures-$D7_CAMPAIGN/dependency_failures.aggregate.json" \
    scenarios=8 false_successes=0 lost_jobs=0 recovery_failures=0 idempotent_recovery=true

run_standard_gate \
    resource_pressure \
    rise2_v2_run_resource_pressure_gate.sh \
    "$D8_CAMPAIGN" \
    "$PILOT_ROOT/resource-pressure-$D8_CAMPAIGN/resource_pressure.aggregate.json" \
    admission_failures=0 unexplained_threshold_breaches=0 \
    cpu_ram_io_disk_observed=true disk_pressure_fail_closed=true

run_standard_gate \
    security_observability \
    rise2_v2_run_security_observability_gate.sh \
    "$D9_CAMPAIGN" \
    "$PILOT_ROOT/security-observability-$D9_CAMPAIGN/security_observability.aggregate.json" \
    fixable_high_critical=0 secret_findings=0 business_identifier_findings=0 \
    public_metrics_blocked=true private_metrics_available=true

run_standard_gate \
    test_data_cleanup \
    rise2_v2_run_test_data_cleanup_gate.sh \
    "$D10_CAMPAIGN" \
    "$PILOT_ROOT/test-data-cleanup-$D10_CAMPAIGN/test_data_cleanup.aggregate.json" \
    remaining_test_users=0 remaining_test_torrents=0 remaining_test_files=0 v1_unchanged=true

if is_passed pilot_accounts; then
    echo "========== SKIP pilot_accounts: ALREADY PASSED =========="
else
    run_standard_gate \
        pilot_accounts \
        rise2_v2_run_pilot_accounts_gate.sh \
        "$D11_CAMPAIGN" \
        "$PILOT_ROOT/pilot-accounts-$D11_CAMPAIGN/pilot_accounts.aggregate.json" \
        pilot_account_count=1 v1_data_moves=0 credentials_in_output=0 \
        forced_credential_change=true v1_unchanged=true
fi

PILOT_CREDENTIAL_FILE="$(find "$PILOT_ROOT" -maxdepth 1 -type f \
    -name 'pilot-credentials-*.json' -printf '%f\n' | sort)"
PILOT_CREDENTIAL_COUNT="$(printf '%s\n' "$PILOT_CREDENTIAL_FILE" | awk 'NF {n++} END {print n+0}')"
[[ "$PILOT_CREDENTIAL_COUNT" == 1 ]] || {
    echo "expected exactly one private pilot credential file" >&2
    exit 1
}
PILOT_CREDENTIAL_FILE="$(printf '%s\n' "$PILOT_CREDENTIAL_FILE" | awk 'NF {print; exit}')"
[[ "$(stat -c '%U:%G:%a' "$PILOT_ROOT/$PILOT_CREDENTIAL_FILE")" == "root:root:600" ]] || {
    echo "pilot credentials are not root-owned 0600" >&2
    exit 1
}
PILOT_CAMPAIGN="${PILOT_CREDENTIAL_FILE#pilot-credentials-}"
PILOT_CAMPAIGN="${PILOT_CAMPAIGN%.json}"
[[ "$PILOT_CAMPAIGN" =~ ^[a-z0-9][a-z0-9-]{0,15}$ ]] || {
    echo "invalid stored pilot campaign" >&2
    exit 1
}

if is_passed rollback; then
    echo "========== SKIP rollback: ALREADY PASSED =========="
else
    assert_unrecorded rollback
    CURRENT_GATE="rollback"
    echo
    echo "========== START rollback =========="
    RTO_SECONDS="$(python3 - "$LEDGER" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    ledger=json.load(stream)
value=ledger["checks"]["backup_restore"]["metrics"].get("rto_seconds")
assert isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
print(value)
PY
)"
    ROLLBACK_EVIDENCE="$PILOT_ROOT/rollback-$D12_CAMPAIGN/rollback.aggregate.json"
    STARTED="$(date +%s)"
    /bin/bash "$SCRIPT_DIR/rise2_v2_run_rollback_gate.sh" \
        --runtime-revision "$RUNTIME_REV" \
        --tool-revision "$TOOL_REV" \
        --campaign "$D12_CAMPAIGN" \
        --pilot-campaign "$PILOT_CAMPAIGN" \
        --v1-health-url "$V1_HEALTH_URL"
    ENDED="$(date +%s)"
    DURATION="$((ENDED - STARTED))"
    [[ -s "$ROLLBACK_EVIDENCE" && ! -L "$ROLLBACK_EVIDENCE" ]] || {
        echo "missing rollback evidence" >&2
        exit 1
    }
    record_gate rollback "$DURATION" "$ROLLBACK_EVIDENCE" \
        "rto_seconds=$RTO_SECONDS" health_failures=0 authentication_failures=0 v1_writes=0 \
        v1_available=true v2_admission_suspended=true v2_volumes_preserved=true
fi

CURRENT_GATE="final_validation"
python3 "$PILOT" validate "$LEDGER"
python3 - "$LEDGER" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    ledger=json.load(stream)
assert len(ledger["checks"]) == 12
assert all(entry.get("status") == "passed" for entry in ledger["checks"].values())
assert ledger["decision"] is None
PY

trap - ERR EXIT
echo
ledger_summary
echo
echo "========================================"
echo "V2-33 GATES 7-12: ALL PASSED"
echo "PILOT LEDGER = 12/12 PASSED"
echo "decision=NOT_RECORDED"
echo "pilot_credentials_file=$PILOT_ROOT/$PILOT_CREDENTIAL_FILE"
echo "========================================"
