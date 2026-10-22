"""Run the real deployment shell against disposable Git + fake Docker processes.

No host paths, real containers, secrets, network, or production data are touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import TypedDict, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "ghcr.io/thomasperez91/world-of-seeds-v2"
OLD_IMAGE = REPOSITORY + "@sha256:" + "1" * 64
NEW_DIGEST = "sha256:" + "2" * 64

FAKE_DOCKER = r"""#!/usr/bin/env python3
import json,os,sys,time,signal,uuid
from pathlib import Path
p=Path(os.environ['FAKE_STATE'])
s=json.loads(p.read_text()); a=sys.argv[1:]
s['log'].append(a)
def save(): p.write_text(json.dumps(s))
def die(): save();sys.exit(1)
def out(v): save();print(v);sys.exit(0)
mode=s['mode']
if a[0]=='compose':
 if a[1]=='version':out('Docker Compose version test')
 a=a[a.index('-f')+2:]
 if a[0]=='config':
  if mode=='config':die()
  out(json.dumps({'services':{'worker':{'deploy':{'replicas':2}}}}))
 if a[0]=='ps':
  name=a[-1]
  if name=='worker':out('worker1\nworker2' if s['running'] else '')
  if name=='scheduler':out('scheduler' if s['running'] else '')
  out(name)
 if a[0]=='stop':
  s['running']=False;s['stopped']=True
  if mode=='stop' and not s.get('stop_failed'):s['stop_failed']=True;die()
  save();sys.exit(0)
 if a[0]=='run':
  s['migration']=True
  if mode=='migration':die()
  if mode=='partial':s['schema']='changed';die()
  s['head']='new_head';s['schema']='changed'
  if mode=='signal':
   save();os.kill(os.getppid(),signal.SIGTERM);sys.exit(0)
  save();sys.exit(0)
 if a[0]=='up':
  env=Path(os.environ['FAKE_ENV']).read_text()
  target=('2'*64) in env
  s['up'].append('target' if target else 'old')
  if target and mode in ('start','rollback') :die()
  if not target and mode=='rollback':die()
  s['running']=True;s['active']='target' if target else 'old'
  save();sys.exit(0)
if a[0]=='login':sys.stdin.read();out('')
if a[0]=='pull':out('')
if a[:2]==['image','inspect']:
 out(os.environ['FAKE_TARGET'] if 'revision' in a[3] else '2.4.1')
if a[0]=='run':
 out(('old_head' if '1'*64 in a else 'new_head')+' (head)')
if a[0]=='inspect':
 if '.Config.Image' in a[2]:out(REPO+'@sha256:'+'2'*64)
 if 'revision' in a[2]:out(os.environ['FAKE_TARGET'])
 if '.State' in a[2]:out('unhealthy' if mode=='health' and s['active']=='target' else 'healthy')
if a[0]=='exec':
 cmd=a[-1]
 if 'pg_isready' in cmd:out('')
 if 'pg_dump' in cmd:
  key=uuid.uuid4().hex
  out('-- header\n\\restrict '+key+'\nCREATE TABLE '+s['schema']+';\n\\unrestrict '+key)
 if 'version_num' in cmd:
  if mode=='db-unavailable' and s.get('stopped'):die()
  out('unexpected_head' if mode=='head-drift' else s['head'])
 if a[2]=='-i':sys.stdin.read();out('')
out('')
""".replace("REPO+'@sha256:'", repr(REPOSITORY) + "+'@sha256:'")


def command(args: list[str], cwd: Path) -> str:
    return subprocess.check_output(args, cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()


class DockerState(TypedDict):
    mode: str
    head: str
    schema: str
    running: bool
    active: str
    up: list[str]
    log: list[list[str]]


class Deployment:
    def __init__(
        self,
        tmp: Path,
        mode: str,
        *,
        migrate: bool = True,
        compatible: bool = False,
        policy_allowed: bool = True,
        transactional: bool = True,
    ):
        self.path = tmp
        self.repo = tmp / "repo"
        self.repo.mkdir()
        (self.repo / "deploy").mkdir()
        (self.repo / "scripts").mkdir()
        self.environment = tmp / "environment"
        self.environment.write_text("WOS_V2_IMAGE=" + OLD_IMAGE + "\nWOS_V2_PUBLIC_HOST=wos.test\n")
        self.environment.chmod(0o600)
        self.state = tmp / "docker-state.json"
        self.state.write_text(
            json.dumps(
                {
                    "mode": mode,
                    "head": "old_head",
                    "schema": "original",
                    "running": True,
                    "active": "old",
                    "up": [],
                    "log": [],
                }
            )
        )
        (self.repo / "VERSION").write_text("2.3.29\n")
        (self.repo / "deploy/compose.rise2.v2.yaml").write_text("services: {}\n")
        preflight = self.repo / "scripts/rise2_v2_preflight.sh"
        preflight.write_text('#!/bin/sh\n[ "$FAKE_MODE" != preflight ]\n')
        preflight.chmod(0o755)
        (self.repo / "scripts/rise2_v2_database_backup.py").write_text(
            "import os,sys\nfrom pathlib import Path\n"
            'Path(os.environ["FAKE_BACKUP"]).write_text("restore-verified")\n'
            'sys.exit(1 if os.environ["FAKE_MODE"]=="backup" else 0)\n'
        )
        (self.repo / "deploy/deploy-world-of-seeds-v2-rise2").write_bytes(
            (ROOT / "deploy/deploy-world-of-seeds-v2-rise2").read_bytes()
        )
        policy = {
            "schema": 1,
            "upgrades": {
                "old_head->new_head": {
                    "transactional": transactional,
                    "backward_compatible": compatible,
                }
            },
        }
        if not policy_allowed:
            policy["upgrades"] = {}
        (self.repo / "deploy/migration-policy.json").write_text(json.dumps(policy))
        command(["git", "init", "-b", "master"], self.repo)
        command(["git", "config", "user.email", "test@example.invalid"], self.repo)
        command(["git", "config", "user.name", "Test"], self.repo)
        command(["git", "add", "."], self.repo)
        command(["git", "commit", "-m", "old"], self.repo)
        self.previous = command(["git", "rev-parse", "HEAD"], self.repo)
        (self.repo / "VERSION").write_text("2.4.1\n")
        command(["git", "commit", "-am", "target"], self.repo)
        self.target = command(["git", "rev-parse", "HEAD"], self.repo)
        remote = tmp / "remote.git"
        command(["git", "clone", "--bare", str(self.repo), str(remote)], tmp)
        command(["git", "remote", "add", "origin", str(remote)], self.repo)
        command(["git", "checkout", "--detach", self.previous], self.repo)
        binary = tmp / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text(
            FAKE_DOCKER.replace(
                "('old_head' if '1'*64 in a else 'new_head')",
                "('old_head' if any('1'*64 in x for x in a) else '"
                + ("new_head" if migrate else "old_head")
                + "')",
            )
        )
        docker.chmod(0o755)
        for name, body in [("mountpoint", "exit 0"), ("curl", "printf 200"), ("sleep", "exit 0")]:
            file = binary / name
            file.write_text("#!/bin/sh\n" + body + "\n")
            file.chmod(0o755)
        source = (ROOT / "deploy/deploy-world-of-seeds-v2-rise2").read_text()
        source = (
            source.replace("/opt/world-of-seeds-v2", str(self.repo))
            .replace("/etc/world-of-seeds-v2/environment", str(self.environment))
            .replace("/var/lib/world-of-seeds-v2/deploy", str(tmp / "state"))
            .replace(
                "/etc/world-of-seeds-v2/.environment.pre-deploy.",
                str(tmp / ".environment.pre-deploy."),
            )
        )
        source = source.replace(
            '[[ ${EUID} -eq 0 ]] || fail "ce script doit être exécuté par root via sudo."', ":"
        )
        source = source.replace(
            '[[ $(stat -c \'%U:%G\' "${ENV_FILE}") == "root:root" ]]', "[[ 1 == 1 ]]"
        ).replace("-o root -g root", "")
        # Exhaust retries quickly with the fake clock; keep the production branch logic.
        source = source.replace("seq 1 60", "seq 1 3").replace("seq 1 30", "seq 1 3")
        self.helper = tmp / "deploy.sh"
        self.helper.write_text(source)
        self.env = {
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "FAKE_STATE": str(self.state),
            "FAKE_TARGET": self.target,
            "FAKE_MODE": mode,
            "FAKE_ENV": str(self.environment),
            "FAKE_BACKUP": str(tmp / "backup-proof"),
        }

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.helper), self.target, NEW_DIGEST, "test-user"],
            input="fake-registry-token-for-offline-tests\n",
            text=True,
            capture_output=True,
            env=self.env,
            timeout=30,
        )

    def result(self) -> DockerState:
        return cast(DockerState, json.loads(self.state.read_text()))


@pytest.mark.parametrize("mode", ["preflight", "config", "backup", "head-drift"])
def test_preflight_failures_never_stop_runtime(tmp_path: Path, mode: str) -> None:
    d = Deployment(tmp_path, mode)
    r = d.run()
    assert r.returncode != 0, r.stdout + r.stderr
    state = d.result()
    assert state["running"] is True
    assert state["up"] == []
    assert not any("stop" in call for call in state["log"])
    assert OLD_IMAGE in d.environment.read_text()


@pytest.mark.parametrize("mode", ["stop", "migration", "start", "health", "signal"])
def test_failures_restore_old_runtime_without_downgrade(tmp_path: Path, mode: str) -> None:
    d = Deployment(tmp_path, mode, compatible=True)
    r = d.run()
    assert r.returncode != 0, r.stdout + r.stderr
    state = d.result()
    assert state["running"] is True, r.stderr
    assert state["up"][-1] == "old", r.stderr
    assert state["up"].count("old") == 1, r.stderr
    assert "downgrade" not in repr(state["log"])
    assert OLD_IMAGE in d.environment.read_text()
    assert command(["git", "rev-parse", "HEAD"], d.repo) == d.previous
    assert "Rollback applicatif vérifié" in r.stderr


@pytest.mark.parametrize("mode", ["partial", "start", "signal", "db-unavailable"])
def test_unknown_or_incompatible_schema_is_not_downgraded_or_started(
    tmp_path: Path, mode: str
) -> None:
    d = Deployment(tmp_path, mode)
    r = d.run()
    assert r.returncode != 0
    assert "Schéma inconnu/incompatible" in r.stderr
    state = d.result()
    assert "old" not in state["up"]
    assert state["running"] is False
    assert list(tmp_path.glob(".environment.pre-deploy.*"))
    assert "downgrade" not in repr(state["log"])


def test_rollback_failure_is_explicit_and_preserves_recovery(tmp_path: Path) -> None:
    d = Deployment(tmp_path, "rollback", compatible=True)
    r = d.run()
    assert r.returncode != 0
    assert "Rollback applicatif incomplet" in r.stderr
    assert list(tmp_path.glob(".environment.pre-deploy.*"))


def test_no_migration_when_head_identical_and_success_is_recorded(tmp_path: Path) -> None:
    d = Deployment(tmp_path, "ok", migrate=False)
    r = d.run()
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "backup-proof").exists()
    assert not any("migrate" in call for call in d.result()["log"])
    assert "revision=" + d.target in (tmp_path / "state/current.env").read_text()
    assert not list(tmp_path.glob(".environment.pre-deploy.*"))


def test_migration_requires_backup_and_explicit_policy(tmp_path: Path) -> None:
    d = Deployment(tmp_path, "ok", compatible=True)
    r = d.run()
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "backup-proof").read_text() == "restore-verified"
    state = d.result()
    assert state["head"] == "new_head"
    assert state["running"] is True


def test_deployment_lock_rejects_concurrent_invocation(tmp_path: Path) -> None:
    import fcntl

    d = Deployment(tmp_path, "ok", migrate=False)
    directory = tmp_path / "state"
    directory.mkdir()
    with (directory / "deployment.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = d.run()
    assert r.returncode != 0
    assert "autre déploiement" in r.stderr
    assert d.result()["log"] == [["compose", "version"]]


def test_unknown_schema_transition_refuses_before_backup_or_stop(tmp_path: Path) -> None:
    d = Deployment(tmp_path, "ok", policy_allowed=False)
    result = d.run()
    assert result.returncode != 0
    assert d.result()["running"] is True
    assert not (tmp_path / "backup-proof").exists()
    assert not any("stop" in call for call in d.result()["log"])


@pytest.mark.parametrize("valid_checksum", [False, True])
def test_operator_install_verifies_pinned_bytes_and_keeps_checkout(
    tmp_path: Path, valid_checksum: bool
) -> None:
    d = Deployment(tmp_path, "ok", migrate=False)
    directory = tmp_path / "sbin"
    directory.mkdir(mode=0o755)
    target = directory / "deploy-world-of-seeds-v2-rise2"
    target.write_text("old-helper")
    source = (ROOT / "scripts/rise2_v2_deploy_helper_install.sh").read_text()
    source = source.replace("/opt/world-of-seeds-v2", str(d.repo))
    source = source.replace("/usr/local/sbin", str(directory))
    source = source.replace("/var/lib/world-of-seeds-v2/deploy", str(tmp_path / "state"))
    source = source.replace("[[ ${EUID} -eq 0 ]]", "[[ 1 == 1 ]]")
    source = source.replace("== root:root", "!= NEVER")
    source = source.replace("-o root -g root", "")
    source = source.replace('chown root:root "$stage"', ":")
    installer = tmp_path / "install.sh"
    installer.write_text(source)
    expected = hashlib.sha256(
        (ROOT / "deploy/deploy-world-of-seeds-v2-rise2").read_bytes()
    ).hexdigest()
    result = subprocess.run(
        ["bash", str(installer), d.target, expected if valid_checksum else "0" * 64],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (result.returncode == 0) is valid_checksum, result.stderr
    assert command(["git", "rev-parse", "HEAD"], d.repo) == d.previous
    if valid_checksum:
        assert target.read_bytes() == (ROOT / "deploy/deploy-world-of-seeds-v2-rise2").read_bytes()
        assert target.stat().st_mode & 0o777 == 0o755
    else:
        assert target.read_text() == "old-helper"
    assert not list(directory.glob(".wos-v2-deploy.*"))


def test_nontransactional_transition_is_never_automatic(tmp_path: Path) -> None:
    d = Deployment(tmp_path, "ok", transactional=False)
    result = d.run()
    assert result.returncode != 0
    assert d.result()["running"] is True
    assert not (tmp_path / "backup-proof").exists()
    assert not any("stop" in call for call in d.result()["log"])
