#!/usr/bin/env bash

# stdout is only the immutable reference; diagnostics and pull output use stderr
# so callers can capture the result without hiding the reason for a failure.
set -Eeuo pipefail

readonly base_tag="${1:?Usage: resolve-python-base.sh docker.io/library/python:<tag>}"
readonly base_repository="docker.io/library/python"
readonly base_platform="linux/amd64"

fail() {
    printf 'ERROR: Python base resolution: %s\n' "$*" >&2
    exit 1
}

[[ "$base_tag" =~ ^docker\.io/library/python:[A-Za-z0-9_.-]+$ ]] \
    || fail "expected an explicit Docker Official Python image tag"

printf 'Base requested: %s\n' "$base_tag" >&2
printf 'Base platform: %s\n' "$base_platform" >&2
docker pull --platform "$base_platform" "$base_tag" >&2 \
    || fail "docker pull failed for $base_tag ($base_platform); no fallback is allowed"

if ! actual_platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$base_tag")"; then
    fail "docker image inspect could not determine the pulled platform"
fi
[[ "$actual_platform" == "$base_platform" ]] \
    || fail "unexpected pulled platform: $actual_platform (expected $base_platform)"

if ! repo_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' "$base_tag")"; then
    fail "docker image inspect could not read the pulled RepoDigest"
fi
case "${repo_digest%@*}" in
    python|library/python|docker.io/library/python) ;;
    *) fail "missing or unexpected Python RepoDigest: $repo_digest" ;;
esac
base_digest="${repo_digest##*@}"
[[ "$base_digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || fail "invalid pulled digest: $base_digest"
python_base="$base_repository@$base_digest"

printf 'Base resolved digest: %s\n' "$base_digest" >&2
printf 'Base immutable reference: %s\n' "$python_base" >&2
printf '%s\n' "$python_base"
