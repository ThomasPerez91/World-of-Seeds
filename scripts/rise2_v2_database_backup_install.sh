#!/bin/sh
# Run explicitly on Rise2 as root; the app deployment does not run this helper.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run this helper as root.' >&2; exit 1; }
repo=/opt/world-of-seeds-v2
config=/etc/world-of-seeds-v2
for tool in docker age age-keygen python3 systemctl; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 1; }
done
[ -f "$repo/scripts/rise2_v2_database_backup.py" ] || { echo 'Rise2 checkout missing.' >&2; exit 1; }
[ -f "$config/environment" ] || { echo 'Rise2 environment missing.' >&2; exit 1; }
[ ! -L "$config" ] || { echo 'Config directory must not be a symlink.' >&2; exit 1; }
umask 077
# Existing public recipient can point to an externally held key. Never replace it.
if [ ! -e "$config/backup-recipient" ]; then
  [ ! -L "$config/backup-identity" ] || { echo 'Invalid identity path.' >&2; exit 1; }
  if [ ! -e "$config/backup-identity" ]; then
    age-keygen -o "$config/backup-identity"
  fi
  chmod 0600 "$config/backup-identity"
  age-keygen -y "$config/backup-identity" > "$config/backup-recipient"
fi
[ ! -L "$config/backup-recipient" ] || { echo 'Invalid recipient path.' >&2; exit 1; }
chmod 0600 "$config/backup-recipient"
python3 - "$repo" <<'PYTHON'
import runpy
import sys
from pathlib import Path
helper = runpy.run_path(sys.argv[1] + '/scripts/rise2_v2_database_backup.py')
helper['private_directory'](helper['ROOT'])
metrics = helper['METRICS'].parent
if metrics.resolve() != metrics or metrics.is_symlink():
    raise SystemExit('Textfile directory must not contain symlinks')
PYTHON
install -d -m 0755 /var/lib/world-of-seeds-v2/node-exporter-textfile
# First successful dump AND restore required before scheduling anything.
python3 "$repo/scripts/rise2_v2_database_backup.py" backup --recipient-file "$config/backup-recipient"
install -m 0644 "$repo/deploy/world-of-seeds-v2-database-backup.service" /etc/systemd/system/
install -m 0644 "$repo/deploy/world-of-seeds-v2-database-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now world-of-seeds-v2-database-backup.timer
systemctl list-timers world-of-seeds-v2-database-backup.timer --no-pager
echo 'First local encrypted database backup verified; daily timer enabled.'
echo 'Copy the recovery identity and encrypted archives off-host; configure and test notification delivery.'
