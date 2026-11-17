#!/usr/bin/env bash
# CI check: render the Alertmanager template with sample values, then let the pinned image
# validate it and prove where each severity is routed.
set -euo pipefail

repository="$(cd -- "$(dirname -- "$0")/.." && pwd)"
overlay="$repository/deploy/compose.rise2.observability.v2.yaml"
image="$(awk '$1 == "image:" && $2 ~ /^prom\/alertmanager:/ {print $2}' "$overlay")"
[[ "$image" =~ ^prom/alertmanager:v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "pinned Alertmanager image not found in the overlay" >&2
  exit 1
}

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
chmod 0750 "$work"
(
  umask 077
  cat >"$work/settings.json" <<'JSON'
{
  "smtp_smarthost": "smtp.example.org:587",
  "smtp_from": "World of Seeds <alerts@example.org>",
  "smtp_auth_username": "alerts@example.org",
  "email_to": ["ops@example.org"]
}
JSON
  printf '%s' 'https://discord.com/api/webhooks/123456789012345678/ci-example-token' \
    >"$work/discord_webhook_url"
  printf '%s' 'ci-example-password' >"$work/smtp_password"
)

python3 "$repository/scripts/rise2_v2_alertmanager_render.py" \
  --directory "$work" \
  --template "$repository/monitoring/alertmanager/alertmanager.yml.template" \
  --skip-ownership

amtool() {
  docker run --rm --network none --user "$(id -u):$(id -g)" --entrypoint amtool \
    --volume "$work:/etc/alertmanager/private:ro" "$image" "$@"
}
config=/etc/alertmanager/private/alertmanager.yml

amtool check-config "$config"
amtool config routes test --config.file="$config" --verify.receivers=discord-and-email \
  alertname=WOSDatabaseBackupFailed severity=critical
amtool config routes test --config.file="$config" --verify.receivers=discord \
  alertname=WOSHttpDownloadErrors severity=warning
amtool config routes test --config.file="$config" --verify.receivers=blackhole \
  alertname=Watchdog severity=none
echo "Alertmanager configuration and routing verified with $image"
