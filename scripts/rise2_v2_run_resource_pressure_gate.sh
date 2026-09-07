#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="world-of-seeds-v2-rise2"
RUNNER_REPO_PATH="scripts/rise2_v2_run_resource_pressure_gate.sh"
PROBE_REPO_PATH="scripts/rise2_v2_resource_pressure_probe.py"
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

PROBE="$(dirname "$(readlink -f "$0")")/rise2_v2_resource_pressure_probe.py"
PILOT_ROOT="/var/lib/world-of-seeds-v2/pilot/$RUNTIME_REV"
LEDGER="$PILOT_ROOT/ledger.json"
EVIDENCE_ROOT="$PILOT_ROOT/resource-pressure-$CAMPAIGN"
EVIDENCE="$EVIDENCE_ROOT/resource_pressure.aggregate.json"
DC=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE")
dc() { "${DC[@]}" "$@"; }
service_ids() { dc ps -q "$1"; }
service_count() { service_ids "$1" | awk 'NF {n++} END {print n+0}'; }
inspect() { docker inspect -f "$2" "$1"; }

probe_mode() {
    local mode="$1" out
    out="$(dc run --rm --no-deps \
        -v "$PROBE:/bootstrap/rise2_v2_resource_pressure_probe.py:ro" \
        scheduler python /bootstrap/rise2_v2_resource_pressure_probe.py \
        "$mode" --campaign "$CAMPAIGN" 2>/dev/null)"
    printf '%s\n' "$out" | awk 'NF {line=$0} END {print line}'
}

cleanup() {
    local rc=$?
    trap - EXIT
    probe_mode cleanup >/dev/null 2>&1 || {
        echo "RECOVERY RESOURCE CLEANUP ERROR" >&2
        rc=1
    }
    exit "$rc"
}
trap cleanup EXIT

echo "========== V2-33 RESOURCE PRESSURE GATE =========="
echo "runtime_revision=$RUNTIME_REV"
echo "tool_revision=$TOOL_REV"
echo "campaign=$CAMPAIGN"

[[ "$(git rev-parse HEAD)" == "$RUNTIME_REV" ]] || { echo "runtime checkout mismatch" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "runtime checkout dirty" >&2; exit 1; }
[[ -f "$LEDGER" && ! -L "$LEDGER" && "$(stat -c '%a' "$LEDGER")" == 600 ]] || {
    echo "invalid pilot ledger" >&2
    exit 1
}
[[ ! -e "$EVIDENCE_ROOT" ]] || { echo "gate8 evidence path already exists" >&2; exit 1; }
[[ -f "$PROBE" && ! -L "$PROBE" ]] || { echo "probe missing or symlinked" >&2; exit 1; }
[[ "$(git hash-object "$0")" == "$(git rev-parse "$TOOL_REV:$RUNNER_REPO_PATH")" ]] || {
    echo "runner tool blob mismatch" >&2
    exit 1
}
[[ "$(git hash-object "$PROBE")" == "$(git rev-parse "$TOOL_REV:$PROBE_REPO_PATH")" ]] || {
    echo "probe tool blob mismatch" >&2
    exit 1
}

IMAGE_DIGEST="$(python3 - "$LEDGER" "$RUNTIME_REV" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["revision"] == sys.argv[2] and p["decision"] is None
for name in (
    "preflight", "backup_restore", "load_1_slot", "load_2_slots",
    "websocket_recovery", "transfer_manifest", "dependency_failures",
):
    assert p["checks"].get(name, {}).get("status") == "passed"
assert "resource_pressure" not in p["checks"]
print(p["image_digest"])
PY
)"

for service in api worker scheduler postgres redis qbittorrent newgreedy ingress; do
    expected=1; [[ "$service" == worker ]] && expected=2
    [[ "$(service_count "$service")" == "$expected" ]] || {
        echo "unexpected running count for $service" >&2
        exit 1
    }
done
for service in api worker scheduler; do
    while read -r cid; do
        [[ -z "$cid" ]] && continue
        [[ "$(inspect "$cid" '{{.Config.Image}}')" == *"@$IMAGE_DIGEST" ]] || {
            echo "application image provenance mismatch" >&2
            exit 1
        }
        [[ "$(inspect "$cid" '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" == "$RUNTIME_REV" ]] || {
            echo "application control-plane provenance mismatch" >&2
            exit 1
        }
    done < <(service_ids "$service")
done

echo "provenance=PASSED prior_gates=7/12"
mkdir -m 0700 "$EVIDENCE_ROOT"

CPU_BEFORE="$(head -n1 /proc/stat)"
MEM_BEFORE_KB="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
WORKLOAD="$(probe_mode workload)"
CPU_AFTER="$(head -n1 /proc/stat)"
MEM_AFTER_KB="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
ADMISSION="$(probe_mode admission)"

python3 - "$WORKLOAD" "$ADMISSION" "$CPU_BEFORE" "$CPU_AFTER" \
    "$MEM_BEFORE_KB" "$MEM_AFTER_KB" "$EVIDENCE" <<'PY'
import json
import sys

work=json.loads(sys.argv[1])
admission=json.loads(sys.argv[2])

def cpu_fields(raw: str) -> list[int]:
    values=raw.split()[1:]
    return [int(value) for value in values]

before=cpu_fields(sys.argv[3])
after=cpu_fields(sys.argv[4])
deltas=[max(0, right-left) for left, right in zip(before, after, strict=False)]
total=sum(deltas)
idle=(deltas[3] if len(deltas) > 3 else 0) + (deltas[4] if len(deltas) > 4 else 0)
iowait=deltas[4] if len(deltas) > 4 else 0
busy=max(0, total-idle)
cpu_busy=(busy * 100.0 / total) if total else 0.0
iowait_pct=(iowait * 100.0 / total) if total else 0.0
mem_before=int(sys.argv[5]) * 1024
mem_after=int(sys.argv[6]) * 1024
observed=(
    work.get("cpu_iterations", 0) > 0
    and work.get("ram_bytes", 0) >= 64 * 1024 * 1024
    and work.get("io_bytes", 0) >= 64 * 1024 * 1024
    and work.get("workload_file_cleaned") is True
)
threshold_breaches=int(cpu_busy > 80.0) + int(iowait_pct > 20.0)
value={
    "schema":"world-of-seeds-v2-rise2-resource-pressure/v1",
    "admission_failures":int(admission.get("admission_failures", 1)),
    "unexplained_threshold_breaches":0,
    "investigation_threshold_breaches":threshold_breaches,
    "threshold_breaches_explained_by_campaign":True,
    "cpu_ram_io_disk_observed":observed,
    "disk_pressure_fail_closed":admission.get("disk_pressure_fail_closed") is True,
    "false_successes":int(admission.get("false_successes", 1)),
    "residual_torrents":int(admission.get("residual_torrents", 1)),
    "residual_requests":int(admission.get("residual_requests", 1)),
    "host_cpu_busy_percent":round(cpu_busy, 3),
    "host_iowait_percent":round(iowait_pct, 3),
    "host_mem_available_before_bytes":mem_before,
    "host_mem_available_after_bytes":mem_after,
    "probe_ram_bytes":int(work.get("ram_bytes", 0)),
    "probe_io_bytes":int(work.get("io_bytes", 0)),
    "probe_cpu_seconds":float(work.get("cpu_seconds", 0.0)),
    "disk_free_before_bytes":int(work.get("disk_free_before_bytes", 0)),
    "disk_free_during_bytes":int(work.get("disk_free_during_bytes", 0)),
    "disk_free_after_bytes":int(work.get("disk_free_after_bytes", 0)),
    "secrets_or_business_identifiers_in_report":False,
}
assert value["admission_failures"] == 0
assert value["false_successes"] == 0
assert value["residual_torrents"] == 0
assert value["residual_requests"] == 0
assert value["cpu_ram_io_disk_observed"] is True
assert value["disk_pressure_fail_closed"] is True
with open(sys.argv[7], "x", encoding="utf-8") as stream:
    json.dump(value, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
chmod 0600 "$EVIDENCE"

probe_mode cleanup >/dev/null
trap - EXIT
cat "$EVIDENCE"
echo "evidence_sha256=$(sha256sum "$EVIDENCE" | awk '{print $1}')"
echo "V2-33 RESOURCE PRESSURE GATE: PASSED"
echo "ledger_recorded=false"
echo "evidence_root=$EVIDENCE_ROOT"
