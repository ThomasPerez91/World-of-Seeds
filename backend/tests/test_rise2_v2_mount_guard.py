from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
PREFLIGHT = REPOSITORY / "scripts" / "rise2_v2_preflight.sh"
SYSTEMD_UNIT = REPOSITORY / "deploy" / "world-of-seeds-v2-rise2.service"
NEWGREEDY_SMOKE = REPOSITORY / "scripts" / "rise2_v2_newgreedy_smoke.sh"


def test_preflight_checks_mountpoint_before_compose() -> None:
    script = PREFLIGHT.read_text(encoding="utf-8")
    guard = (
        'mountpoint -q -- "$storage" '
        '|| fail "storage directory must be an active mountpoint"'
    )

    assert guard in script
    assert script.index(guard) < script.index("compose() {")


def test_systemd_unit_requires_storage_mount_before_compose() -> None:
    unit = SYSTEMD_UNIT.read_text(encoding="utf-8")
    lines = unit.splitlines()

    assert "Requires=docker.service" in lines
    assert "After=network-online.target docker.service" in lines
    assert "RequiresMountsFor=/srv/world-of-seeds-v2/data" in lines
    assert "WorkingDirectory=/opt/world-of-seeds-v2" in lines
    assert "ExecStartPre=/usr/bin/mountpoint -q /srv/world-of-seeds-v2/data" in lines
    assert (
        "ExecStartPre=/opt/world-of-seeds-v2/scripts/rise2_v2_preflight.sh "
        "/etc/world-of-seeds-v2/environment"
    ) in lines
    assert (
        "ExecStart=/usr/bin/docker compose --env-file /etc/world-of-seeds-v2/environment "
        "-f /opt/world-of-seeds-v2/deploy/compose.rise2.v2.yaml up --detach --wait "
        "--wait-timeout 180"
    ) in lines
    assert (
        "ExecStop=/usr/bin/docker compose --env-file /etc/world-of-seeds-v2/environment "
        "-f /opt/world-of-seeds-v2/deploy/compose.rise2.v2.yaml stop"
    ) in lines
    assert "RemainAfterExit=yes" in lines
    assert "TimeoutStopSec=35min" in lines


def test_systemd_stop_preserves_pilot_state() -> None:
    unit = SYSTEMD_UNIT.read_text(encoding="utf-8")

    assert " down" not in unit
    assert "--volumes" not in unit
    assert "docker.sock" not in unit


def test_newgreedy_ci_smoke_uses_a_real_temporary_mount() -> None:
    script = NEWGREEDY_SMOKE.read_text(encoding="utf-8")
    mount = (
        "sudo mount -t tmpfs -o size=16m,mode=0750,uid=10001,gid=10001 "
        '\\\n    tmpfs "$smoke_root/data"'
    )

    assert mount in script
    assert 'mountpoint -q -- "$smoke_root/data" || fail "CI storage tmpfs mount failed"' in script
    assert 'sudo umount -- "$smoke_root/data"' in script
    assert script.index('sudo umount -- "$smoke_root/data"') < script.index(
        'sudo rm -rf -- "$smoke_root"'
    )
