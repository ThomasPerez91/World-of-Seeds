#!/usr/bin/env bash

set -Eeuo pipefail

readonly upstream_url="https://github.com/Mrt0t0/NewGreedy.git"
readonly upstream_source="https://github.com/Mrt0t0/NewGreedy"
readonly base_tag="docker.io/library/python:3.11-slim"
readonly pip_tools_version="7.6.1"
readonly workspace="${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
readonly requested_ref="${NEWGREEDY_REF:?NEWGREEDY_REF is required}"
readonly requested_version="${NEWGREEDY_VERSION:?NEWGREEDY_VERSION is required}"
readonly run_suffix="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"

upstream_dir="$workspace/upstream"
context_dir="$workspace/newgreedy-build-context"
artifact_dir="$workspace/newgreedy-artifacts"
runtime_a="$workspace/newgreedy-runtime-a"
runtime_b="$workspace/newgreedy-runtime-b"
inspection_dir="$workspace/newgreedy-inspection"
container_a="wos-newgreedy-a-$run_suffix"
container_b="wos-newgreedy-b-$run_suffix"
audit_container="wos-newgreedy-audit-$run_suffix"
volume_a="wos-newgreedy-ca-a-$run_suffix"
volume_b="wos-newgreedy-ca-b-$run_suffix"

cleanup() {
    set +e
    docker rm -f "$container_a" "$container_b" "$audit_container" \
        >/dev/null 2>&1
    docker volume rm "$volume_a" "$volume_b" >/dev/null 2>&1
}
trap cleanup EXIT

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

wait_healthy() {
    local container="$1"
    local status
    local attempt

    for attempt in $(seq 1 60); do
        status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container")"
        case "$status" in
            healthy)
                return 0
                ;;
            unhealthy)
                docker logs "$container" >&2
                fail "$container became unhealthy"
                ;;
        esac
        if [[ "$(docker inspect --format '{{.State.Running}}' "$container")" != "true" ]]; then
            docker logs "$container" >&2
            fail "$container stopped before becoming healthy"
        fi
        sleep 2
    done

    docker logs "$container" >&2
    fail "$container did not become healthy in time"
}

start_runtime() {
    local container="$1"
    local volume="$2"
    local runtime_dir="$3"

    docker run --detach \
        --name "$container" \
        --mount "type=volume,src=$volume,dst=/root/.mitmproxy" \
        --mount "type=bind,src=$runtime_dir/config.ini,dst=/app/config.ini,readonly" \
        --mount "type=bind,src=$runtime_dir/stats.json,dst=/app/stats.json" \
        --mount "type=bind,src=$runtime_dir/torrent_registry.json,dst=/app/torrent_registry.json" \
        --mount "type=bind,src=$runtime_dir/newgreedy.log,dst=/app/newgreedy.log" \
        --publish 127.0.0.1::8080 \
        "$local_ref" >/dev/null
    wait_healthy "$container"

    local host_port
    host_port="$(docker port "$container" 8080/tcp | awk -F: 'END {print $NF}')"
    [[ "$host_port" =~ ^[0-9]+$ ]] || fail "could not resolve the web port for $container"
    curl -fsS "http://127.0.0.1:$host_port/api/health" >/dev/null
    docker exec "$container" python3 -c \
        'import socket; socket.create_connection(("127.0.0.1", 3456), 5).close()'
}

prepare_runtime() {
    local runtime_dir="$1"

    mkdir -p "$runtime_dir"
    cp "$upstream_dir/config.ini" "$runtime_dir/config.ini"
    if grep -Eiq '(passkey|c411|password|api[_-]?key|access[_-]?token|client[_-]?secret|credential)' \
        "$runtime_dir/config.ini"; then
        fail "the upstream test configuration contains a secret-like field"
    fi
    printf '{}\n' >"$runtime_dir/stats.json"
    printf '{}\n' >"$runtime_dir/torrent_registry.json"
    : >"$runtime_dir/newgreedy.log"
}

[[ "$requested_ref" =~ ^[0-9A-Za-z][0-9A-Za-z._/+@-]{0,255}$ ]] \
    || fail "newgreedy_ref contains unsupported characters"
[[ "$requested_ref" != *'..'* && "$requested_ref" != *'@{'* && "$requested_ref" != *'//'* ]] \
    || fail "newgreedy_ref is not a safe explicit Git ref"
[[ "$requested_version" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] \
    || fail "newgreedy_version is not a supported version string"

rm -rf "$upstream_dir" "$context_dir" "$artifact_dir" "$runtime_a" "$runtime_b" "$inspection_dir"
mkdir -p "$upstream_dir" "$context_dir" "$artifact_dir" "$inspection_dir"

git -C "$upstream_dir" init --quiet
git -C "$upstream_dir" remote add origin "$upstream_url"
git -C "$upstream_dir" fetch --quiet --no-tags --depth=1 origin "$requested_ref"
git -C "$upstream_dir" checkout --quiet --detach FETCH_HEAD

resolved_commit="$(git -C "$upstream_dir" rev-parse HEAD)"
[[ "$resolved_commit" =~ ^[0-9a-f]{40}$ ]] || fail "resolved commit is not a full Git SHA"
git -C "$upstream_dir" cat-file -e "$resolved_commit^{commit}"
[[ "$(git -C "$upstream_dir" remote get-url origin)" == "$upstream_url" ]] \
    || fail "resolved commit did not come from the expected upstream"

echo "Requested ref: $requested_ref"
echo "Resolved commit: $resolved_commit"
echo "Requested version: $requested_version"

python3 - "$upstream_dir" "$requested_version" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
requested = sys.argv[2].removeprefix("v")
files = ("newgreedy.py", "newgreedy_addon.py", "newgreedy_web.py")
versions = set()
for name in files:
    path = root / name
    if not path.is_file():
        raise SystemExit(f"required upstream version source is missing: {name}")
    match = re.search(
        r'^VERSION\s*=\s*["\']v?([^"\']+)["\']\s*$',
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise SystemExit(f"reliable upstream version is missing from {name}")
    versions.add(match.group(1))
if versions != {requested}:
    raise SystemExit(
        f"requested version {requested!r} does not match upstream declarations {sorted(versions)!r}"
    )
print(f"Verified upstream version: {requested}")
PY

rsync --archive --delete \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='config.ini' \
    --exclude='stats.json' \
    --exclude='torrent_registry.json' \
    --exclude='purge_pending.json' \
    --exclude='newgreedy.log' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pem' \
    --exclude='*.key' \
    --exclude='*.p12' \
    --exclude='*.pfx' \
    "$upstream_dir/" "$context_dir/"

if find "$context_dir" -type l -print -quit | grep -q .; then
    fail "the sanitized build context contains a symbolic link"
fi
if find "$context_dir" -type f \( \
    -name 'config.ini' -o \
    -name 'stats.json' -o \
    -name 'torrent_registry.json' -o \
    -name 'purge_pending.json' -o \
    -name 'newgreedy.log' -o \
    -name '*.pyc' -o \
    -name '*.pem' -o \
    -name '*.key' -o \
    -name '*.p12' -o \
    -name '*.pfx' \
    \) -print -quit | grep -q .; then
    fail "the sanitized build context contains forbidden runtime material"
fi
if find "$context_dir" -type d \( -name '.git' -o -name '__pycache__' \) -print -quit | grep -q .; then
    fail "the sanitized build context contains forbidden metadata"
fi
if grep -RIlE '(C411|passkey|BEGIN ([A-Z]+ )?PRIVATE KEY)' "$context_dir" | grep -q .; then
    fail "the sanitized build context contains secret-like material"
fi

base_digest="$(
    docker buildx imagetools inspect "$base_tag" \
        | awk '$1 == "Digest:" {print $2; exit}'
)"
[[ "$base_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "could not resolve the Python base digest"
python_base="$base_tag@$base_digest"
echo "Resolved Python base: $python_base"

docker run --rm --platform linux/amd64 \
    --mount "type=bind,src=$upstream_dir,dst=/src,readonly" \
    --mount "type=bind,src=$context_dir,dst=/out" \
    "$python_base" \
    sh -euc "
        python -m pip install --no-cache-dir 'pip-tools==$pip_tools_version'
        pip-compile \
            --generate-hashes \
            --resolver=backtracking \
            --strip-extras \
            --output-file=/out/requirements.lock \
            /src/requirements.txt
    "
[[ -s "$context_dir/requirements.lock" ]] || fail "the Python dependency lock was not generated"

local_ref="world-of-seeds-newgreedy:test-$resolved_commit"
docker buildx build \
    --platform linux/amd64 \
    --load \
    --provenance=false \
    --file "$workspace/ops/newgreedy/Dockerfile" \
    --build-arg "PYTHON_BASE_IMAGE=$python_base" \
    --label "org.opencontainers.image.source=$upstream_source" \
    --label "org.opencontainers.image.revision=$resolved_commit" \
    --label "org.opencontainers.image.version=${requested_version#v}" \
    --label "org.opencontainers.image.licenses=GPL-3.0-only" \
    --label "org.opencontainers.image.base.name=$base_tag" \
    --label "org.opencontainers.image.base.digest=$base_digest" \
    --label "org.opencontainers.image.title=World of Seeds NewGreedy runtime" \
    --label "org.opencontainers.image.description=Unofficial NewGreedy packaging for World of Seeds infrastructure" \
    --label "com.world-of-seeds.image.role=newgreedy-runtime" \
    --label "com.world-of-seeds.upstream.official=false" \
    --tag "$local_ref" \
    "$context_dir"

[[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$local_ref")" == "linux/amd64" ]] \
    || fail "the built image is not linux/amd64"
[[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$local_ref")" == "$resolved_commit" ]] \
    || fail "the image revision label is incorrect"
[[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$local_ref")" == "${requested_version#v}" ]] \
    || fail "the image version label is incorrect"

prepare_runtime "$runtime_a"
prepare_runtime "$runtime_b"
docker volume create "$volume_a" >/dev/null
docker volume create "$volume_b" >/dev/null

start_runtime "$container_a" "$volume_a" "$runtime_a"
docker exec "$container_a" test -s /root/.mitmproxy/mitmproxy-ca-cert.pem
docker exec "$container_a" test -s /root/.mitmproxy/mitmproxy-ca.pem
docker exec "$container_a" grep -Eq 'BEGIN (RSA |EC )?PRIVATE KEY' \
    /root/.mitmproxy/mitmproxy-ca.pem
ca_a_before="$(docker exec "$container_a" sha256sum /root/.mitmproxy/mitmproxy-ca-cert.pem | awk '{print $1}')"
[[ "$ca_a_before" =~ ^[0-9a-f]{64}$ ]] || fail "the first CA fingerprint is invalid"

docker restart "$container_a" >/dev/null
wait_healthy "$container_a"
restart_port="$(docker port "$container_a" 8080/tcp | awk -F: 'END {print $NF}')"
[[ "$restart_port" =~ ^[0-9]+$ ]] || fail "could not resolve the web port after restart"
curl -fsS "http://127.0.0.1:$restart_port/api/health" >/dev/null
docker exec "$container_a" python3 -c \
    'import socket; socket.create_connection(("127.0.0.1", 3456), 5).close()'
ca_a_after="$(docker exec "$container_a" sha256sum /root/.mitmproxy/mitmproxy-ca-cert.pem | awk '{print $1}')"
[[ "$ca_a_after" == "$ca_a_before" ]] || fail "the CA changed after a restart with the same volume"

start_runtime "$container_b" "$volume_b" "$runtime_b"
docker exec "$container_b" test -s /root/.mitmproxy/mitmproxy-ca-cert.pem
docker exec "$container_b" test -s /root/.mitmproxy/mitmproxy-ca.pem
docker exec "$container_b" grep -Eq 'BEGIN (RSA |EC )?PRIVATE KEY' \
    /root/.mitmproxy/mitmproxy-ca.pem
ca_b="$(docker exec "$container_b" sha256sum /root/.mitmproxy/mitmproxy-ca-cert.pem | awk '{print $1}')"
[[ "$ca_b" =~ ^[0-9a-f]{64}$ ]] || fail "the second CA fingerprint is invalid"
[[ "$ca_b" != "$ca_a_before" ]] || fail "a fresh volume reused the first runtime CA"

audit_container="$(docker create "$local_ref")"
docker export "$audit_container" >"$inspection_dir/rootfs.tar"
tar -tf "$inspection_dir/rootfs.tar" >"$artifact_dir/rootfs-files.txt"
if grep -Eq '(^|/)(root/\.mitmproxy(/|$)|app/config\.ini$|app/stats\.json$|app/torrent_registry\.json$|app/purge_pending\.json$|app/newgreedy\.log$|app/\.git(/|$)|app/__pycache__(/|$)|app/.*\.pyc$)' \
    "$artifact_dir/rootfs-files.txt"; then
    fail "the final image filesystem contains forbidden runtime or source material"
fi

image_id="$(docker image inspect --format '{{.Id}}' "$local_ref")"
[[ "$image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "the tested image ID is invalid"
docker save --output "$inspection_dir/tested-image.tar" "$local_ref"
mkdir -p "$inspection_dir/image-save"
tar -xf "$inspection_dir/tested-image.tar" -C "$inspection_dir/image-save"
jq -r '.[0].Layers[]' "$inspection_dir/image-save/manifest.json" >"$inspection_dir/layers.txt"
while IFS= read -r layer; do
    tar -tf "$inspection_dir/image-save/$layer" >"$inspection_dir/layer-files.txt"
    if grep -Eq '(^|/)(root/\.mitmproxy(/|$)|app/config\.ini$|app/stats\.json$|app/torrent_registry\.json$|app/purge_pending\.json$|app/newgreedy\.log$|app/\.git(/|$)|app/__pycache__(/|$)|app/.*\.pyc$)' \
        "$inspection_dir/layer-files.txt"; then
        fail "a raw image layer contains forbidden runtime or source material"
    fi
done <"$inspection_dir/layers.txt"

if docker history --no-trunc "$local_ref" \
    | grep -Ei '(C411|passkey|BEGIN ([A-Z]+ )?PRIVATE KEY)' \
    | grep -q .; then
    fail "the image history contains secret-like material"
fi

docker run --rm "$local_ref" \
    dpkg-query -W -f='${binary:Package}=${Version}\n' \
    | LC_ALL=C sort >"$artifact_dir/apt-packages.txt"
docker run --rm "$local_ref" python -m pip freeze --all \
    | LC_ALL=C sort >"$artifact_dir/pip-freeze.txt"
cp "$context_dir/requirements.lock" "$artifact_dir/requirements.lock"
cp "$inspection_dir/tested-image.tar" "$artifact_dir/tested-image.tar"
gzip --no-name "$artifact_dir/tested-image.tar"
sha256sum "$artifact_dir/tested-image.tar.gz" \
    | sed 's#  .*/#  #' >"$artifact_dir/tested-image.tar.gz.sha256"
image_archive_sha="$(awk '{print $1}' "$artifact_dir/tested-image.tar.gz.sha256")"
lock_sha="$(sha256sum "$artifact_dir/requirements.lock" | awk '{print $1}')"

cat >"$artifact_dir/build-metadata.env" <<EOF
REQUESTED_REF=$requested_ref
RESOLVED_COMMIT=$resolved_commit
REQUESTED_VERSION=${requested_version#v}
BASE_DIGEST=$base_digest
LOCAL_REF=$local_ref
IMAGE_ID=$image_id
IMAGE_ARCHIVE_SHA256=$image_archive_sha
REQUIREMENTS_LOCK_SHA256=$lock_sha
CA_A_BEFORE=$ca_a_before
CA_A_AFTER=$ca_a_after
CA_B=$ca_b
EOF

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
        echo "resolved_commit=$resolved_commit"
        echo "version=${requested_version#v}"
        echo "base_digest=$base_digest"
        echo "local_ref=$local_ref"
        echo "image_id=$image_id"
        echo "image_archive_sha=$image_archive_sha"
        echo "lock_sha=$lock_sha"
        echo "ca_a_before=$ca_a_before"
        echo "ca_a_after=$ca_a_after"
        echo "ca_b=$ca_b"
    } >>"$GITHUB_OUTPUT"
fi

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    cat >>"$GITHUB_STEP_SUMMARY" <<EOF
## Validated NewGreedy image

- Requested ref: \`$requested_ref\`
- Resolved commit: \`$resolved_commit\`
- Requested version: \`${requested_version#v}\`
- Python base digest: \`$base_digest\`
- Tested image ID: \`$image_id\`
- Dependency lock SHA-256: \`$lock_sha\`
- CA fingerprint before restart: \`$ca_a_before\`
- CA fingerprint after restart: \`$ca_a_after\`
- Fresh-volume CA fingerprint: \`$ca_b\`
- Raw layers and the exported root filesystem contain no NewGreedy runtime CA, config or state files.
- The exact tested Docker image was exported for the publish job; no rebuild is permitted.
EOF
fi

echo "Validated image archive SHA-256: $image_archive_sha"
