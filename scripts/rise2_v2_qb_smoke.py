#!/usr/bin/env python3
"""Real, disposable Rise2 qB/NewGreedy acceptance. Never targets the pilot project."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
WOS_IMAGE = (
    "ghcr.io/thomasperez91/world-of-seeds-v2@sha256:"
    "ac883e493d4ad12fed7ab88a42b3911196365cca09d9ebe1364f5011fe431d43"
)
NEWGREEDY_IMAGE = (
    "ghcr.io/thomasperez91/world-of-seeds-newgreedy@sha256:"
    "7f737a5133ac71b1b346df93e6fad11ef2d2744ab3021870aae634227ad9429f"
)


def run(command: list[str], label: str, *, stdin: str | None = None) -> str:
    result = subprocess.run(command, input=stdin, capture_output=True, text=True, check=False)
    if result.returncode:
        # Raw Compose/qB output can contain a temporary password if bootstrap fails.
        # Only the dedicated probe/preflight emits bounded, value-free diagnostics.
        safe = [
            line
            for line in (result.stdout + result.stderr).splitlines()
            if line.startswith(
                ("qB probe failed:", "qB bootstrap failed:", "Rise2 V2 preflight failed:")
            )
        ]
        for marker in (
            "read-only",
            "read only",
            "secret",
            "environment variable",
            "permission denied",
            "executable file not found",
            "no such file",
            "network",
            "OCI runtime",
        ):
            if marker in (result.stdout + result.stderr).lower():
                safe.append("diagnostic category=" + marker)
        raise RuntimeError(label + " failed" + (": " + " ".join(safe) if safe else ""))
    return result.stdout


def smoke() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("run with sudo on a disposable Docker test host")
    parent = Path("/srv/world-of-seeds-v2")
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="ci-qb-", dir=parent))
    project = "world-of-seeds-v2-qb-smoke-" + secrets.token_hex(6)
    # Include the real preflight's nested Compose invocations in this project.
    os.environ["COMPOSE_PROJECT_NAME"] = project
    environment = root / "environment"
    data = root / "data"
    compose = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(environment),
        "-f",
        str(REPOSITORY / "deploy/compose.rise2.v2.yaml"),
    ]
    mounted = False
    try:
        root.chmod(0o755)
        data.mkdir(mode=0o750)
        run(
            [
                "mount",
                "-t",
                "tmpfs",
                "-o",
                "size=16m,mode=0750,uid=10001,gid=10001",
                "tmpfs",
                str(data),
            ],
            "isolated test storage mount",
        )
        mounted = True
        state = root / "newgreedy-state"
        state.mkdir(mode=0o700)
        ng_config = root / "newgreedy.ini"
        ng_config.write_text(
            "[proxy]\nlisten_port = 3456\n[stats]\npersist_stats = true\n"
            "auto_purge_stopped = false\n[web]\nweb_enabled = true\n"
            "web_host = 0.0.0.0\nweb_port = 8080\n"
        )
        ng_config.chmod(0o640)
        os.chown(ng_config, 10001, 10003)
        username = "qb-test-" + secrets.token_hex(5)
        password = secrets.token_urlsafe(32) + "$literal"
        registry = json.dumps(
            {
                "routes": [
                    {
                        "tracker_account_ref": "11111111-1111-4111-8111-111111111111",
                        "qbittorrent_account_ref": "22222222-2222-4222-8222-222222222222",
                        "newgreedy_url": "http://newgreedy:8080",
                        "c411_passkey": secrets.token_urlsafe(32),
                        "qbittorrent_url": "http://qbittorrent:8080",
                        "qbittorrent_username": username,
                        "qbittorrent_password": password,
                    }
                ]
            }
        )
        values = {
            "WOS_V2_IMAGE": WOS_IMAGE,
            "WOS_V2_NEWGREEDY_IMAGE": NEWGREEDY_IMAGE,
            "WOS_V2_PUBLIC_HOST": "v2-ci.example.invalid",
            "WOS_V2_GRAFANA_HOST": "monitoring-ci.example.invalid",
            "WOS_V2_ALLOWED_HOSTS": '["v2-ci.example.invalid","api","127.0.0.1"]',
            "WOS_V2_STORAGE_HOST_PATH": str(data),
            "WOS_V2_POSTGRES_DB": "world_of_seeds_v2",
            "WOS_V2_POSTGRES_USER": "world_of_seeds_v2",
            "WOS_V2_POSTGRES_PASSWORD": secrets.token_urlsafe(32),
            "WOS_V2_INTEGRATION_ACCOUNTS_JSON": "'" + registry + "'",
            "WOS_V2_QBITTORRENT_CONFIG_PATH": str(root / "qBittorrent.conf"),
            "WOS_V2_NEWGREEDY_CONFIG_PATH": str(ng_config),
            "WOS_V2_NEWGREEDY_STATE_HOST_PATH": str(state),
            "WOS_V2_APP_UID": "10001",
            "WOS_V2_APP_GID": "10001",
            "WOS_V2_QBITTORRENT_UID": "10001",
            "WOS_V2_QBITTORRENT_GID": "10002",
            "WOS_V2_NEWGREEDY_GID": "10003",
            "WOS_V2_WORKER_REPLICAS": "1",
            "WOS_V2_GRAFANA_ADMIN_USER": "unused-test-admin",
            "WOS_V2_GRAFANA_ADMIN_PASSWORD": secrets.token_urlsafe(32),
        }
        environment.write_text("\n".join(key + "=" + value for key, value in values.items()) + "\n")
        environment.chmod(0o600)
        run(
            ["sh", str(REPOSITORY / "scripts/rise2_v2_preflight.sh"), str(environment)],
            "real preflight",
        )
        normalized = run(compose + ["config", "--format", "json"], "Compose normalization")
        if password in normalized or username in normalized or registry in normalized:
            raise RuntimeError("Compose rendered integration credentials")
        print("PASS: preflight, derived hash, Compose secret redaction", flush=True)

        probe_source = (REPOSITORY / "scripts/rise2_v2_qb_probe.py").read_text()

        def probe(*arguments: str) -> None:
            output = run(
                compose
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--entrypoint",
                    "python",
                    "worker",
                    "-",
                    *arguments,
                ],
                "internal WOS-like qB probe",
                stdin=probe_source,
            )
            if not output.startswith("PASS:"):
                raise RuntimeError("probe did not produce its acceptance result")
            print(output.strip(), flush=True)

        def healthy() -> str:
            for service in ("newgreedy", "qbittorrent"):
                identifier = run(
                    compose + ["ps", "-q", service], "container identification"
                ).strip()
                info = json.loads(run(["docker", "inspect", identifier], "container inspection"))[0]
                if info["State"].get("Health", {}).get("Status") != "healthy" or any(
                    info["NetworkSettings"]["Ports"].values()
                ):
                    raise RuntimeError("service health or unpublished-port invariant failed")
            return identifier

        run(
            compose + ["up", "-d", "--wait", "--wait-timeout", "180", "qbittorrent"],
            "fresh startup",
        )
        qb_id = healthy()
        probe("--set-sentinel")
        logs = run(compose + ["logs", "--no-color", "qbittorrent"], "qB log leak check")
        if password in logs or username in logs or "temporary password" in logs.lower():
            raise RuntimeError("qB startup emitted authentication material")
        public_ca = run(
            compose + ["exec", "-T", "qbittorrent", "cat", "/wos-ca/mitmproxy-ca-cert.pem"],
            "public CA inspection",
        )
        bundle = run(
            compose + ["exec", "-T", "qbittorrent", "cat", "/etc/ssl/certs/ca-certificates.crt"],
            "qB CA bundle inspection",
        )
        if public_ca.strip() not in bundle or "PRIVATE KEY" in public_ca:
            raise RuntimeError("qB trust bundle does not contain only the exported public CA")
        run(
            compose
            + [
                "exec",
                "-T",
                "newgreedy",
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "30",
                "--proxy",
                "http://127.0.0.1:3456",
                "--cacert",
                "/root/.mitmproxy/mitmproxy-ca-cert.pem",
                "https://example.com/",
            ],
            "real NewGreedy HTTPS proxy",
        )
        print(
            "PASS: fresh volume, NewGreedy CA in qB bundle, real HTTPS proxy, no host ports",
            flush=True,
        )
        run(compose + ["restart", "qbittorrent"], "qB restart")
        run(
            compose + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "qbittorrent"],
            "restart readiness",
        )
        healthy()
        probe("--sentinel")
        print("PASS: restart and unrelated preference preserved", flush=True)
        run(
            compose
            + [
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "180",
                "qbittorrent",
            ],
            "qB force-recreate",
        )
        qb_id = healthy()
        probe("--sentinel")
        print("PASS: force-recreate and existing profile preserved", flush=True)

        # Hold worker/scheduler mounts across an atomic credential rotation.
        readers = []
        for service in ("worker", "scheduler"):
            reader = project + "-rotation-" + service
            run(
                compose
                + [
                    "run",
                    "-d",
                    "--no-deps",
                    "--name",
                    reader,
                    "--entrypoint",
                    "python",
                    service,
                    "-c",
                    "import time; time.sleep(600)",
                ],
                "persistent credential reader creation",
            )
            readers.append(reader)
        run(compose + ["stop", "qbittorrent"], "coordinated rotation qB stop")
        run(["docker", "stop", *readers], "coordinated rotation WOS stop")
        payload = json.loads(registry)
        password = secrets.token_urlsafe(32) + "$rotated"
        payload["routes"][0]["qbittorrent_password"] = password
        registry = json.dumps(payload)
        values["WOS_V2_INTEGRATION_ACCOUNTS_JSON"] = "'" + registry + "'"
        environment.write_text("\n".join(key + "=" + value for key, value in values.items()) + "\n")
        run(
            ["sh", str(REPOSITORY / "scripts/rise2_v2_preflight.sh"), str(environment)],
            "credential rotation preflight",
        )
        run(
            compose + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "qbittorrent"],
            "credential rotation qB restart",
        )
        if healthy() != qb_id:
            raise RuntimeError("rotation must exercise the existing qB container mount")
        run(["docker", "start", *readers], "credential rotation WOS restart")
        for reader in readers:
            fingerprint = run(
                [
                    "docker",
                    "exec",
                    reader,
                    "python",
                    "-c",
                    "import hashlib; from pathlib import Path; "
                    "print(hashlib.sha256(Path('/run/secrets/integration_registry').read_bytes()).hexdigest())",
                ],
                "existing WOS mount freshness",
            ).strip()
            if fingerprint != hashlib.sha256(registry.encode()).hexdigest():
                raise RuntimeError("existing WOS container retained stale credential inode")
            run(
                ["docker", "exec", "-i", reader, "python", "-", "--sentinel"],
                "rotated existing WOS authentication",
                stdin=probe_source,
            )
        run(["docker", "rm", "-f", *readers], "disposable credential readers cleanup")
        print("PASS: atomic rotation reaches existing qB, worker and scheduler mounts", flush=True)

        for migration in (None, 5):
            run(compose + ["stop", "qbittorrent"], "legacy test profile stop")
            legacy = (
                (root / "qBittorrent.conf")
                .read_text()
                .replace("MigrationVersion=8", "" if migration is None else "MigrationVersion=5")
                .replace(
                    "[Preferences]",
                    "[Preferences]\nConnection\\ProxyType=0\n"
                    "Connection\\Proxy\\IP=obsolete.invalid\nConnection\\Proxy\\Port=9\n"
                    "Connection\\ProxyOnlyForTorrents=false\nConnection\\ProxyPeerConnections=true\n"
                    "Downloads\\SavePath=/obsolete",
                )
                .replace("[Network]", "[Network]\nProxy\\OnlyForTorrents=false")
            )
            legacy = legacy.replace(
                "[BitTorrent]",
                "[BitTorrent]\nSession\\ProxyHostnameLookup=false\nSession\\MaxConnections=137",
            )
            run(
                compose
                + [
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--entrypoint",
                    "/bin/sh",
                    "qbittorrent-init",
                    "-ec",
                    "cat > /config/qBittorrent/config/qBittorrent.conf",
                ],
                "disposable legacy profile restoration",
                stdin=legacy,
            )
            run(
                compose
                + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "qbittorrent"],
                "legacy profile migration startup",
            )
            healthy()
            probe("--sentinel")
        print("PASS: absent/pre-v6 migration markers preserve policy on first startup", flush=True)
        info = json.loads(run(["docker", "inspect", qb_id], "test volume resolution"))[0]
        volume = next(
            m["Name"]
            for m in info["Mounts"]
            if m["Destination"] == "/config" and m["Type"] == "volume"
        )
        metadata = json.loads(
            run(["docker", "volume", "inspect", volume], "test volume authorization")
        )[0]
        labels = metadata.get("Labels", {})
        if (
            labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.volume") != "qbittorrent_v2_config"
            or not volume.startswith(project + "_")
        ):
            raise RuntimeError("refusing to wipe a volume outside the disposable test project")
        run(compose + ["stop", "qbittorrent"], "test qB stop")
        run(compose + ["rm", "-f", "qbittorrent", "qbittorrent-init"], "test container removal")
        run(["docker", "volume", "rm", volume], "explicit disposable qB volume wipe")
        run(
            compose + ["up", "-d", "--wait", "--wait-timeout", "180", "qbittorrent"],
            "post-wipe startup",
        )
        healthy()
        probe()
        print("PASS: qB-only wipe and full automatic reconstruction", flush=True)
    finally:
        if environment.exists():
            subprocess.run(
                compose + ["down", "--volumes", "--remove-orphans"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        if mounted:
            run(["umount", str(data)], "test storage unmount")
        if root.parent == parent and root.name.startswith("ci-qb-"):
            shutil.rmtree(root)


if __name__ == "__main__":
    try:
        smoke()
    except RuntimeError as error:
        sys.exit("Rise2 qB smoke failed: " + str(error))
