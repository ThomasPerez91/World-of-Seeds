"""Behaviour of scripts/rise2_accounts_gcs.sh with stubbed docker, age, gcloud and id."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/rise2_accounts_gcs.sh"
RECIPIENT = "age1" + "q" * 58
SNAPSHOT = '{"version":1,"accounts":[],"discord_links":[]}'

STUBS = {
    "id": '#!/bin/sh\n[ "$1" = "-u" ] && echo 0 || exec /usr/bin/id "$@"\n',
    "docker": (
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >"$STUB_LOG/docker"\n'
        '[ -z "$FAIL_DOCKER" ] || exit 3\n'
        f"printf '%s' '{SNAPSHOT}'\n"
    ),
    "age": (
        "#!/bin/sh\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in --recipient) recipient="$2"; shift 2;; --output) output="$2"; shift 2;;'
        " *) exit 9;; esac\n"
        "done\n"
        '{ printf "age-encryption.org/v1 %s\\n" "$recipient"; cat; } >"$output"\n'
    ),
    "gcloud": (
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >"$STUB_LOG/gcloud"\n'
        '[ -z "$FAIL_GCLOUD" ] || exit 4\n'
        'for arg; do case "$arg" in /*) source="$arg";; esac; done\n'
        'cp "$source" "$FAKE_BUCKET/$(basename "$source")"\n'
    ),
}


@pytest.fixture
def host(tmp_path: Path) -> dict[str, Path]:
    if shutil.which("flock") is None:
        pytest.skip("flock is required")
    paths = {
        name: tmp_path / name
        for name in ("bin", "logs", "bucket", "config", "repository", "workdir", "textfile")
    }
    for name in ("bin", "logs", "bucket", "config", "textfile"):
        paths[name].mkdir()
    (paths["repository"] / "deploy").mkdir(parents=True)
    (paths["repository"] / "deploy/compose.rise2.v2.yaml").write_text("services: {}\n")
    (paths["config"] / "environment").write_text("WOS_V2_POSTGRES_DB=test\n")
    (paths["config"] / "recipient").write_text(RECIPIENT + "\n")
    for name, body in STUBS.items():
        stub = paths["bin"] / name
        stub.write_text(body)
        stub.chmod(0o755)
    return paths


def _run(host: dict[str, Path], **extra: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": f"{host['bin']}:{os.environ['PATH']}",
        "STUB_LOG": str(host["logs"]),
        "FAKE_BUCKET": str(host["bucket"]),
        "WOS_V2_ENV_FILE": str(host["config"] / "environment"),
        "WOS_V2_REPOSITORY": str(host["repository"]),
        "WOS_BACKUP_RECIPIENT_FILE": str(host["config"] / "recipient"),
        "WOS_BACKUP_WORKDIR": str(host["workdir"]),
        "WOS_BACKUP_METRICS": str(host["textfile"] / "wos_accounts_backup.prom"),
        "WOS_BACKUP_BUCKET": "gs://world_of_seeds_bucket",
        **extra,
    }
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=False, timeout=30
    )


def _metrics(host: dict[str, Path]) -> dict[str, int]:
    lines = (host["textfile"] / "wos_accounts_backup.prom").read_text().splitlines()
    return {name: int(value) for name, value in (line.split() for line in lines)}


def _leftovers(host: dict[str, Path]) -> set[str]:
    return {path.name for path in host["workdir"].iterdir()} - {".lock", "last-success"}


def test_export_is_encrypted_in_the_pipe_and_uploaded_without_overwrite(
    host: dict[str, Path],
) -> None:
    result = _run(host)

    assert result.returncode == 0, result.stderr
    docker = (host["logs"] / "docker").read_text()
    assert "--project-name world-of-seeds-v2-rise2" in docker
    assert docker.rstrip().endswith("exec -T api python -m app.account_backup dump")
    assert "--if-generation-match=0" in (host["logs"] / "gcloud").read_text()
    assert "gs://world_of_seeds_bucket/wos-accounts/v1/" in (host["logs"] / "gcloud").read_text()
    (uploaded,) = host["bucket"].iterdir()
    assert uploaded.name.startswith("accounts-") and uploaded.name.endswith(".json.age")
    assert uploaded.read_text() == f"age-encryption.org/v1 {RECIPIENT}\n{SNAPSHOT}"
    assert _leftovers(host) == set()
    metrics = _metrics(host)
    assert metrics["wos_accounts_backup_last_attempt_success"] == 1
    assert metrics["wos_accounts_backup_last_success_timestamp_seconds"] > 0
    assert oct((host["textfile"] / "wos_accounts_backup.prom").stat().st_mode & 0o777) == "0o644"


@pytest.mark.parametrize("failure", ["FAIL_DOCKER", "FAIL_GCLOUD"])
def test_failed_export_or_upload_leaves_no_archive_and_reports_failure(
    host: dict[str, Path], failure: str
) -> None:
    assert _run(host).returncode == 0
    first_success = _metrics(host)["wos_accounts_backup_last_success_timestamp_seconds"]
    for uploaded in host["bucket"].iterdir():
        uploaded.unlink()

    result = _run(host, **{failure: "1"})

    assert result.returncode != 0
    assert list(host["bucket"].iterdir()) == []
    assert _leftovers(host) == set()
    metrics = _metrics(host)
    assert metrics["wos_accounts_backup_last_attempt_success"] == 0
    assert metrics["wos_accounts_backup_last_success_timestamp_seconds"] == first_success


@pytest.mark.parametrize("recipient", ["", "age1short", "AGE-SECRET-KEY-1" + "Q" * 58])
def test_a_missing_or_invalid_recipient_is_reported_as_a_failure(
    host: dict[str, Path], recipient: str
) -> None:
    (host["config"] / "recipient").write_text(recipient)

    result = _run(host)

    assert result.returncode != 0
    assert "recipient" in result.stderr
    assert not (host["logs"] / "docker").exists()
    assert _metrics(host) == {
        "wos_accounts_backup_last_success_timestamp_seconds": 0,
        "wos_accounts_backup_last_attempt_success": 0,
    }


def test_a_concurrent_run_stops_without_touching_the_metrics(host: dict[str, Path]) -> None:
    host["workdir"].mkdir(mode=0o700)
    lock = host["workdir"] / ".lock"
    holder = subprocess.Popen(["flock", "--exclusive", str(lock), "sleep", "10"])
    try:
        for _ in range(50):
            if lock.exists():
                break
            subprocess.run(["sleep", "0.1"], check=True)
        subprocess.run(["sleep", "0.3"], check=True)
        result = _run(host)
    finally:
        holder.kill()
        holder.wait()

    assert result.returncode != 0
    assert "another account backup is running" in result.stderr
    assert not (host["textfile"] / "wos_accounts_backup.prom").exists()
