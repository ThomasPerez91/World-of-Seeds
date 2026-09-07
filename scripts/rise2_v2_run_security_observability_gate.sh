#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_REPO_PATH="scripts/rise2_v2_run_security_observability_gate.sh"
REPO="/opt/world-of-seeds-v2"
ENV_FILE="/etc/world-of-seeds-v2/environment"
COMPOSE="deploy/compose.rise2.v2.yaml"
RUNTIME_REV=""
TOOL_REV=""
CAMPAIGN=""
TRIVY_VERSION="0.74.0"
TRIVY_SHA256="2ae6fe3ee734b7fdf11335663e18c75ea12dccc76062f09f164a3b0f8be4371a"

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

PILOT_ROOT="/var/lib/world-of-seeds-v2/pilot/$RUNTIME_REV"
LEDGER="$PILOT_ROOT/ledger.json"
EVIDENCE_ROOT="$PILOT_ROOT/security-observability-$CAMPAIGN"
EVIDENCE="$EVIDENCE_ROOT/security_observability.aggregate.json"
DC=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE")
dc() { "${DC[@]}" "$@"; }
service_ids() { dc ps -q "$1"; }
inspect() { docker inspect -f "$2" "$1"; }

TMP=""
cleanup() {
    local rc=$?
    trap - EXIT
    [[ -z "$TMP" ]] || rm -rf -- "$TMP"
    exit "$rc"
}
trap cleanup EXIT

echo "========== V2-33 SECURITY / OBSERVABILITY GATE =========="
echo "runtime_revision=$RUNTIME_REV"
echo "tool_revision=$TOOL_REV"
echo "campaign=$CAMPAIGN"

[[ "$(git rev-parse HEAD)" == "$RUNTIME_REV" ]] || { echo "runtime checkout mismatch" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "runtime checkout dirty" >&2; exit 1; }
[[ -f "$LEDGER" && ! -L "$LEDGER" && "$(stat -c '%a' "$LEDGER")" == 600 ]] || {
    echo "invalid pilot ledger" >&2
    exit 1
}
[[ ! -e "$EVIDENCE_ROOT" ]] || { echo "gate9 evidence path already exists" >&2; exit 1; }
[[ "$(git hash-object "$0")" == "$(git rev-parse "$TOOL_REV:$RUNNER_REPO_PATH")" ]] || {
    echo "runner tool blob mismatch" >&2
    exit 1
}

APP_IMAGE="$(python3 - "$LEDGER" "$RUNTIME_REV" <<'PY'
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["revision"] == sys.argv[2] and p["decision"] is None
for name in (
    "preflight", "backup_restore", "load_1_slot", "load_2_slots",
    "websocket_recovery", "transfer_manifest", "dependency_failures", "resource_pressure",
):
    assert p["checks"].get(name, {}).get("status") == "passed"
assert "security_observability" not in p["checks"]
print("ghcr.io/thomasperez91/world-of-seeds-v2@" + p["image_digest"])
PY
)"

for service in api worker scheduler; do
    while read -r cid; do
        [[ -z "$cid" ]] && continue
        [[ "$(inspect "$cid" '{{.Config.Image}}')" == "$APP_IMAGE" ]] || {
            echo "application image provenance mismatch" >&2
            exit 1
        }
        [[ "$(inspect "$cid" '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" == "$RUNTIME_REV" ]] || {
            echo "application control-plane provenance mismatch" >&2
            exit 1
        }
    done < <(service_ids "$service")
done

echo "provenance=PASSED prior_gates=8/12"
mkdir -m 0700 "$EVIDENCE_ROOT"
TMP="$(mktemp -d -p "$PILOT_ROOT" .security-g9.XXXXXX)"
chmod 0700 "$TMP"

ARCHIVE="$TMP/trivy.tar.gz"
TRIVY="$TMP/trivy"
curl --fail --silent --show-error --location \
    --output "$ARCHIVE" \
    "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz"
echo "$TRIVY_SHA256  $ARCHIVE" | sha256sum --check --strict >/dev/null
tar --extract --gzip --no-same-owner --file "$ARCHIVE" --directory "$TMP" trivy
chmod 0700 "$TRIVY"
export TRIVY_CACHE_DIR="$TMP/cache"
mkdir -m 0700 "$TRIVY_CACHE_DIR"
"$TRIVY" --version | head -n1

CI_RUNS="$TMP/ci-runs.json"
curl --fail --silent --show-error --location \
    --output "$CI_RUNS" \
    "https://api.github.com/repos/ThomasPerez91/World-of-Seeds/actions/runs?head_sha=$TOOL_REV&event=pull_request&per_page=20"

"$TRIVY" config \
    --severity HIGH,CRITICAL --exit-code 0 --format json \
    --output "$TMP/config.json" "$REPO/Dockerfile" >/dev/null 2>"$TMP/config.stderr"
"$TRIVY" image \
    --scanners vuln --ignore-unfixed --severity HIGH,CRITICAL --exit-code 0 --format json \
    --output "$TMP/image.json" "$APP_IMAGE" >/dev/null 2>"$TMP/image.stderr"

PUBLIC_HOST="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' \
    "$(service_ids ingress | head -n1)" | awk -F= '$1=="WOS_V2_PUBLIC_HOST" {print substr($0,index($0,"=")+1)}')"
[[ -n "$PUBLIC_HOST" ]] || { echo "unable to resolve V2 public host" >&2; exit 1; }

PUBLIC_CODE="$(curl -k --location --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --max-time 15 \
    --resolve "$PUBLIC_HOST:80:127.0.0.1" \
    --resolve "$PUBLIC_HOST:443:127.0.0.1" \
    "http://$PUBLIC_HOST/api/v2/metrics")"

PRIVATE_METRICS="$(dc run --rm --no-deps scheduler python - <<'PY'
import urllib.request
with urllib.request.urlopen("http://api:8000/api/v2/metrics", timeout=10) as response:
    body=response.read().decode("utf-8")
    if response.status != 200:
        raise SystemExit(2)
    print(body, end="")
PY
)"
printf '%s' "$PRIVATE_METRICS" >"$TMP/private-metrics.txt"

dc logs --since 30m --no-color \
    api worker scheduler ingress prometheus grafana qbittorrent newgreedy \
    >"$TMP/runtime.log" 2>/dev/null || true
chmod 0600 "$TMP"/*.json "$TMP"/*.txt "$TMP"/*.log 2>/dev/null || true

python3 - "$CI_RUNS" "$TMP/config.json" "$TMP/image.json" \
    "$TMP/private-metrics.txt" "$TMP/runtime.log" "$PUBLIC_CODE" "$EVIDENCE" <<'PY'
import json
import re
import sys
from pathlib import Path


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def vulnerability_count(report: dict) -> int:
    return sum(
        1
        for result in report.get("Results") or []
        for item in result.get("Vulnerabilities") or []
        if item.get("Severity") in {"HIGH", "CRITICAL"} and item.get("FixedVersion")
    )


def misconfiguration_count(report: dict) -> int:
    return sum(
        1
        for result in report.get("Results") or []
        for item in result.get("Misconfigurations") or []
        if item.get("Severity") in {"HIGH", "CRITICAL"}
    )


def privacy_counts(text: str) -> tuple[int, int]:
    secret_patterns=(
        r"(?i)(?:password|passkey|authorization|bearer|session_token|csrf_token)\s*[:=]\s*[^\s,]+",
        r"(?i)https?://[^\s]+/announce/[A-Za-z0-9%_.~-]{8,}",
    )
    business_patterns=(
        r"(?i)(?:info_hash|infohash|storage_key|user_id|username)\s*[:=]\s*[A-Za-z0-9._:-]{4,}",
        r"(?i)/data/(?:content|users?)/[^\s]+",
    )
    secrets=sum(len(re.findall(pattern, text)) for pattern in secret_patterns)
    business=sum(len(re.findall(pattern, text)) for pattern in business_patterns)
    return secrets, business


runs=load(sys.argv[1])
config=load(sys.argv[2])
image=load(sys.argv[3])
metrics=Path(sys.argv[4]).read_text(encoding="utf-8", errors="replace")
logs=Path(sys.argv[5]).read_text(encoding="utf-8", errors="replace")
public_code=int(sys.argv[6])
workflow_runs=runs.get("workflow_runs") or []
ci_success=any(
    run.get("name") == "CI" and run.get("conclusion") == "success"
    for run in workflow_runs
)
bootstrap_success=any(
    run.get("name") == "Rise2 qB bootstrap" and run.get("conclusion") == "success"
    for run in workflow_runs
)
config_high=misconfiguration_count(config)
image_fixable=vulnerability_count(image)
secret_metrics, business_metrics=privacy_counts(metrics)
secret_logs, business_logs=privacy_counts(logs)
secret_findings=secret_metrics + secret_logs
business_findings=business_metrics + business_logs
private_available=(
    "# HELP wos_jobs" in metrics
    and "wos_storage_pressure" in metrics
    and "wos_redis_up" in metrics
)
value={
    "schema":"world-of-seeds-v2-rise2-security-observability/v1",
    "fixable_high_critical":config_high + image_fixable,
    "ci_dependency_audits_verified":ci_success,
    "ci_qb_bootstrap_verified":bootstrap_success,
    "configuration_high_critical":config_high,
    "image_fixable_high_critical":image_fixable,
    "secret_findings":secret_findings,
    "business_identifier_findings":business_findings,
    "public_metrics_blocked":public_code == 404,
    "private_metrics_available":private_available,
    "public_metrics_http_status":public_code,
    "runtime_log_secret_findings":secret_logs,
    "runtime_log_business_identifier_findings":business_logs,
    "metrics_secret_findings":secret_metrics,
    "metrics_business_identifier_findings":business_metrics,
    "trivy_version_verified":True,
    "secrets_or_business_identifiers_in_report":False,
}
with open(sys.argv[7], "x", encoding="utf-8") as stream:
    json.dump(value, stream, indent=2, sort_keys=True)
    stream.write("\n")
if not value["ci_dependency_audits_verified"] or not value["ci_qb_bootstrap_verified"]:
    raise SystemExit(9)
if value["fixable_high_critical"] != 0:
    raise SystemExit(10)
if value["secret_findings"] != 0 or value["business_identifier_findings"] != 0:
    raise SystemExit(11)
if not value["public_metrics_blocked"]:
    raise SystemExit(12)
if not value["private_metrics_available"]:
    raise SystemExit(13)
PY

chmod 0600 "$EVIDENCE"
cat "$EVIDENCE"
echo "evidence_sha256=$(sha256sum "$EVIDENCE" | awk '{print $1}')"
echo "V2-33 SECURITY / OBSERVABILITY GATE: PASSED"
echo "ledger_recorded=false"
echo "evidence_root=$EVIDENCE_ROOT"
