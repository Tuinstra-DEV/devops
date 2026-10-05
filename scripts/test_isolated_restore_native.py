#!/usr/bin/env python3
"""Root-only, disposable DEV-33 restore-test integration fixture.

Run explicitly in a disposable Linux runtime with preinstalled pinned images
and age/restic tools. It never downloads images or tools, never uses the
DEV34 production_restore_target adapter, and removes only UUID-named resources
that this invocation created. --keep-fixture retains synthetic credentials and
the local restic repository for a later Console worker-chain rehearsal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


PG_IMAGE = "docker.io/library/postgres:15-alpine@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b"
UMAMI_IMAGE = "ghcr.io/umami-software/umami:3.3.1@sha256:fa32d116cf20cad52cbc3fad9a63b46e7fa02299d8f967168eb453d49c476b4a"
HOST = "tuinstra-prod-01"
APP = "umami"
DOCKER = Path("/usr/bin/docker")  # restore-umami is intentionally pinned to this Linux path.
TWO_FACTOR_KEY = "TWO_FACTOR_ENCRYPTION_KEY"


class HarnessError(RuntimeError):
    pass


def repo_from_file(script: Path, override: str | None) -> Path:
    if override:
        candidate = Path(override).resolve()
        if (candidate / "backup/tuinstra_backup.py").is_file():
            return candidate
        raise HarnessError("--repo does not contain the production backup CLI")
    for parent in (script.resolve().parent, *script.resolve().parents):
        if (parent / "backup/tuinstra_backup.py").is_file():
            return parent
    raise HarnessError("run this test under the DEVOPS repo or pass --repo")


def run(args: list[str], *, env: dict[str, str] | None = None, timeout: int = 180,
        input_bytes: bytes | None = None, stdout: Any = subprocess.PIPE,
        check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(args, env=env, input=input_bytes, stdout=stdout,
                                stderr=subprocess.PIPE, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError(f"{Path(args[0]).name} could not complete") from exc
    if check and result.returncode != 0:
        raise HarnessError(f"{Path(args[0]).name} exited {result.returncode}")
    return result


def docker(args: list[str], *, check: bool = True, timeout: int = 180) -> bytes:
    return run([str(DOCKER), *args], check=check, timeout=timeout).stdout or b""


def root_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def atomic_json(path: Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    temporary.chmod(mode)
    os.replace(temporary, path)


def inventory() -> set[str]:
    output = docker(["ps", "--all", "--format", "{{.Names}}"])
    return {line for line in output.decode().splitlines() if line}


def remove_own_container(name: str) -> None:
    output = docker(["ps", "--all", "--quiet", "--filter", f"name=^/{name}$"], check=False)
    ids = output.decode().splitlines()
    if ids:
        if len(ids) != 1:
            raise HarnessError("owned container name resolved ambiguously; fixture retained")
        docker(["rm", "--force", name], timeout=60)
    if name in inventory():
        raise HarnessError("owned container cleanup failed; fixture retained")


def api_init_script() -> str:
    # All data is synthetic. The credential is the image's initial local-only
    # admin value; no application secret is put in argv or printed.
    return r'''import { createHmac } from 'node:crypto';
const call=async(path,body,token)=>{const r=await fetch('http://127.0.0.1:3000'+path,{method:'POST',headers:{'Content-Type':'application/json',Accept:'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(10000)});const value=await r.json().catch(()=>({}));if(!r.ok)throw new Error('synthetic Umami API returned '+r.status);return value;};
const login=await call('/api/auth/login',{username:'admin',password:'umami'});if(typeof login.token!=='string')throw new Error('initial admin login unavailable');
const setup=await call('/api/2fa/setup/initiate',undefined,login.token);if(typeof setup.manualKey!=='string'||!/^[A-Z2-7]+$/.test(setup.manualKey))throw new Error('2FA seed unavailable');
const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';let bits='';for(const c of setup.manualKey){const n=alphabet.indexOf(c);if(n<0)throw new Error('2FA seed invalid');bits+=n.toString(2).padStart(5,'0');}const key=Buffer.from((bits.match(/.{8}/g)||[]).map(x=>parseInt(x,2)));
const otp=async()=>{await new Promise(resolve=>setTimeout(resolve,30000-Date.now()%30000+1000));const counter=Buffer.alloc(8);counter.writeBigUInt64BE(BigInt(Math.floor(Date.now()/30000)));const h=createHmac('sha1',key).update(counter).digest(),o=h[h.length-1]&15;return (((h[o]&127)<<24|h[o+1]<<16|h[o+2]<<8|h[o+3])%1000000).toString().padStart(6,'0');};
await call('/api/2fa/setup/confirm',{token:await otp()},login.token);const pending=await call('/api/auth/login',{username:'admin',password:'umami'});if(pending.requiresTwoFactor!==true||typeof pending.partialToken!=='string')throw new Error('2FA was not required on subsequent login');const verified=await call('/api/2fa/verify',{token:await otp()},pending.partialToken);if(typeof verified.token!=='string')throw new Error('synthetic TOTP verification failed');const identity=await call('/api/auth/verify',undefined,verified.token);if(identity.username!=='admin')throw new Error('synthetic admin identity mismatch');
console.log('synthetic admin 2FA initialized');'''


def wait_for_source(name: str, script: str | None = None, timeout: int = 100) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if script is None:
            probe = run([str(DOCKER), "exec", name, "pg_isready", "-U", "umami", "-d", "umami"], check=False)
        else:
            probe = run([str(DOCKER), "exec", name, "node", "-e", script], check=False)
        if probe.returncode == 0:
            return
        time.sleep(1)
    raise HarnessError("synthetic source service readiness timed out")


def package_point(root: Path, backup: Any, *, variant: str,
                  recipient: str) -> tuple[dict[str, Any], Path]:
    case_root = root / "points" / variant
    payload = case_root / "payload"
    shutil.copytree(root / "payload", payload)
    internal_path = payload / "backup-manifest.json"
    internal = json.loads(internal_path.read_text(encoding="utf-8"))
    if variant != "success":
        internal["artifact_id"] = str(uuid.uuid4())
        internal["created_at"] = backup.now()
    if variant == "restore-failure":
        (payload / "database.dump").write_bytes(b"not-a-PostgreSQL-custom-format-dump")
    elif variant == "application-failure":
        env_file = payload / "files/umami-env"
        env_file.write_text(env_file.read_text(encoding="utf-8") + "PORT=3001\n", encoding="utf-8")
    inputs = []
    for path in sorted(p for p in payload.rglob("*") if p.is_file() and p != internal_path):
        inputs.append({"name": path.relative_to(payload).as_posix(),
                       "sha256": backup.sha256(path), "bytes": path.stat().st_size})
    internal["inputs"] = inputs
    atomic_json(internal_path, internal)

    archive = case_root / "payload.tar"
    archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tarfile.open(archive, "w") as tar:
        tar.add(payload, arcname="payload", recursive=True)
    artifact_dir = case_root / "artifact"
    artifact_dir.mkdir(mode=0o700, parents=True)
    encrypted = artifact_dir / "payload.age"
    run(["age", "--recipient", recipient, "--output", str(encrypted), str(archive)])
    public = {key: internal[key] for key in (
        "schema_version", "artifact_id", "host_slug", "app_id", "adapter", "created_at")}
    public.update(payload_sha256=backup.sha256(encrypted), payload_bytes=encrypted.stat().st_size)
    if variant == "corrupt-payload":
        damaged = bytearray(encrypted.read_bytes())
        damaged[-1] ^= 0x01
        encrypted.write_bytes(damaged)
        encrypted.chmod(0o600)
        # Deliberately retain the pre-corruption public/catalog checksum.
    atomic_json(artifact_dir / "manifest.json", public)
    return public, artifact_dir


def run_cli(repo: Path, root: Path, config_path: Path, snapshot: str,
            env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    return run([sys.executable, str(repo / "backup/tuinstra_backup.py"),
                "--config", str(config_path), "restore-test", "--host", HOST,
                "--app", APP, "--snapshot", snapshot], env=env, timeout=900,
                check=False)


def instrument_adapter(root: Path, name: str, adapter: Path, event_path: Path,
                       wrapper_basename: str = "restore-umami") -> Path:
    """Wrap a test-only adapter copy, recording only exit code and bounded class."""
    wrapper_dir = root / name
    wrapper_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    wrapper = wrapper_dir / wrapper_basename
    stderr_path = wrapper_dir / "adapter.stderr"
    script = f"""#!/bin/bash
set -uo pipefail
adapter={shlex.quote(str(adapter))}
event={shlex.quote(str(event_path))}
stderr_file={shlex.quote(str(stderr_path))}
set +e
"$adapter" "$@" 2>"$stderr_file"
code=$?
set -e
failure_class=unclassified
if grep -Fq 'isolated Umami did not become healthy' "$stderr_file"; then
  failure_class=application-health
elif grep -Fq 'isolated restore cleanup failed' "$stderr_file"; then
  failure_class=cleanup
elif grep -Fq 'pg_restore' "$stderr_file"; then
  failure_class=pg_restore
fi
printf '{{"exit_code":%s,"failure_class":"%s"}}\\n' "$code" "$failure_class" >"$event"
cat -- "$stderr_file" >&2
rm -f -- "$stderr_file"
exit "$code"
"""
    root_write(wrapper, script.encode(), 0o700)
    return wrapper


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="DEVOPS repo; otherwise derive it from this script's parents")
    parser.add_argument("--keep-fixture", action="store_true",
                        help="retain synthetic credentials/repository for a Console worker-chain run")
    args = parser.parse_args()

    if sys.platform != "linux" or os.geteuid() != 0:
        raise HarnessError("run only as root inside the disposable Linux/Colima VM")
    repo = repo_from_file(Path(__file__), args.repo)
    if not DOCKER.is_file() or not os.access(DOCKER, os.X_OK):
        raise HarnessError("/usr/bin/docker is required by the fixed restore adapter")
    for executable in ("age", "age-keygen", "restic"):
        if shutil.which(executable) is None:
            raise HarnessError(f"installed Linux {executable} binary is required; no install/download performed")
    for image in (PG_IMAGE, UMAMI_IMAGE):
        run([str(DOCKER), "image", "inspect", image], timeout=30)
    docker_root = run([str(DOCKER), "info", "--format", "{{.DockerRootDir}}"], timeout=30).stdout.decode().strip()
    if not docker_root or not Path(docker_root).is_dir():
        raise HarnessError("Docker data root is unavailable for a local capacity check")
    # Images must already exist. The large DB directories are bind-mounted from
    # /tmp and adapter work is tmpfs-backed, so reserve only 256 MiB in Docker's
    # data root, plus separate headroom for /run and the synthetic fixture.
    capacity = {
        "docker_root_free_bytes": shutil.disk_usage(docker_root).free,
        "run_free_bytes": shutil.disk_usage("/run").free,
        "fixture_tmp_free_bytes": shutil.disk_usage("/tmp").free,
    }
    if (capacity["docker_root_free_bytes"] < 256 * 1024 * 1024
            or capacity["run_free_bytes"] < 512 * 1024 * 1024
            or capacity["fixture_tmp_free_bytes"] < 1536 * 1024 * 1024):
        raise HarnessError("capacity gate failed without bypass or prune: "
                           + json.dumps(capacity, sort_keys=True))

    # Unique per-run path: never reuse the extant /tmp/dev33-20261005 fixture.
    root = Path(tempfile.mkdtemp(prefix="dev33-native-", dir="/tmp")).resolve()
    root.chmod(0o700)
    run_id = uuid.uuid4().hex
    db_name = f"dev33-{run_id}-source-db"
    app_name = f"dev33-{run_id}-source-app"
    restore_run_root = Path("/run/tuinstra-backup") / f"dev33-native-{run_id}"
    created_containers: list[str] = []
    retain = args.keep_fixture
    results: list[dict[str, Any]] = []
    summary: dict[str, Any] | None = None
    baseline_inventory: set[str] | None = None
    try:
        import sys as _sys
        _sys.dont_write_bytecode = True
        _sys.path.insert(0, str(repo / "backup"))
        import tuinstra_backup as backup  # type: ignore[import-not-found]

        if any(name in inventory() for name in (db_name, app_name)):
            raise HarnessError("generated source container name unexpectedly exists")
        fixture = root / "payload"
        files = fixture / "files"
        files.mkdir(mode=0o700, parents=True)
        (root / "source-postgres").mkdir(mode=0o700)
        # Official PG image owns its data directory as numeric uid/gid 70.
        os.chown(root / "source-postgres", 70, 70)
        database_password = uuid.uuid4().hex
        two_factor_key = os.urandom(32).hex()
        app_secret = uuid.uuid4().hex + uuid.uuid4().hex
        postgres_env = (f"POSTGRES_DB=umami\nPOSTGRES_USER=umami\n"
                        f"POSTGRES_PASSWORD={database_password}\nTZ=UTC\n").encode()
        umami_env = (f"DATABASE_URL=postgresql://umami:{database_password}@127.0.0.1:5432/umami\n"
                     f"APP_SECRET={app_secret}\n{TWO_FACTOR_KEY}={two_factor_key}\n"
                     "DISABLE_TELEMETRY=1\n").encode()
        values = {
            "postgres-env": postgres_env,
            "umami-env": umami_env,
            "database-password": f"{database_password}\n".encode(),
            "app-secret": f"{app_secret}\n".encode(),
            "two-factor-encryption-key": f"{two_factor_key}\n".encode(),
            "admin-password": b"umami\n",  # local image bootstrap credential only
            "compose": ("services:\n  db:\n    image: " + PG_IMAGE +
                        "\n  umami:\n    image: " + UMAMI_IMAGE + "\n").encode(),
        }
        for name, content in values.items():
            root_write(files / name, content)
        (root / "source-postgres").chmod(0o700)
        docker(["run", "--detach", "--name", db_name, "--network", "none",
                "--env-file", str(files / "postgres-env"), "--mount",
                f"type=bind,source={root / 'source-postgres'},target=/var/lib/postgresql/data",
                "--security-opt", "no-new-privileges:true", PG_IMAGE])
        created_containers.append(db_name)
        if docker(["inspect", "--format", "{{.HostConfig.NetworkMode}}", db_name]).decode().strip() != "none":
            raise HarnessError("synthetic PostgreSQL source is not network-isolated")
        wait_for_source(db_name)
        docker(["run", "--detach", "--name", app_name, "--network", f"container:{db_name}",
                "--env-file", str(files / "umami-env"), "--security-opt", "no-new-privileges:true",
                "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", UMAMI_IMAGE])
        created_containers.append(app_name)
        if docker(["inspect", "--format", "{{.HostConfig.NetworkMode}}", app_name]).decode().strip() != f"container:{docker(['inspect', '--format', '{{.Id}}', db_name]).decode().strip()}":
            raise HarnessError("synthetic Umami source did not share the network-none namespace")
        wait_for_source(app_name, 'fetch("http://127.0.0.1:3000/api/heartbeat").then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))')
        source_script = root / "source-two-factor.mjs"
        root_write(source_script, api_init_script().encode(), 0o600)
        init_result = run([str(DOCKER), "exec", "-i", app_name, "node", "--input-type=module"],
                          input_bytes=source_script.read_bytes(), timeout=90).stdout or b""
        if b"synthetic admin 2FA initialized" not in init_result:
            raise HarnessError("synthetic Umami API 2FA initialization did not confirm")

        marker_bytes = docker(["exec", db_name, "psql", "-X", "-t", "-A", "--set=ON_ERROR_STOP=1",
                               "-U", "umami", "-d", "umami", "-c", backup.UMAMI_CONTENT_MARKER_SQL])
        marker = json.loads(marker_bytes)
        if (not isinstance(marker, dict) or marker.get("admin", {}).get("username") != "admin"
                or marker.get("admin_two_factor", {}).get("is_enabled") is not True
                or not isinstance(marker.get("user_count"), int) or marker["user_count"] < 1
                or not isinstance(marker.get("two_factor_count"), int) or marker["two_factor_count"] < 1):
            raise HarnessError("source DB lacks Umami admin and enabled encrypted 2FA")
        with (fixture / "database.dump").open("wb") as dump:
            run([str(DOCKER), "exec", db_name, "pg_dump", "--format=custom", "-U", "umami", "umami"],
                timeout=120, stdout=dump)
        if (fixture / "database.dump").stat().st_size == 0:
            raise HarnessError("synthetic PostgreSQL dump is empty")

        artifact_id = str(uuid.uuid4())
        created_at = backup.now()
        inputs = []
        for path in sorted(path for path in fixture.rglob("*") if path.is_file()):
            inputs.append({"name": path.relative_to(fixture).as_posix(),
                           "sha256": backup.sha256(path), "bytes": path.stat().st_size})
        internal = {
            "schema_version": backup.SCHEMA_VERSION, "artifact_id": artifact_id,
            "host_slug": HOST, "app_id": APP, "adapter": "postgres-compose-v1",
            "created_at": created_at, "database_service": "db",
            "images": sorted([PG_IMAGE, UMAMI_IMAGE]),
            "image_services": {"db": PG_IMAGE, "umami": UMAMI_IMAGE},
            "database": {
                "engine": "postgresql",
                "server_version": docker(["exec", db_name, "psql", "-X", "-t", "-A", "-U", "umami", "-d", "umami", "-c", "show server_version"]).decode().strip(),
                "dump_version": docker(["exec", db_name, "pg_dump", "--version"]).decode().strip(),
                "dump_format": "custom", "service": "db",
                "content_marker": {"algorithm": backup.UMAMI_CONTENT_MARKER_ALGORITHM,
                                   "sha256": hashlib.sha256(marker_bytes).hexdigest(),
                                   "user_count": marker["user_count"],
                                   "two_factor_count": marker["two_factor_count"]},
            },
            "inputs": inputs,
        }
        backup.atomic_json(fixture / "backup-manifest.json", internal)
        identity = root / "age-identity.txt"
        run(["age-keygen", "-o", str(identity)], timeout=30)
        identity.chmod(0o600)
        recipient = run(["age-keygen", "-y", str(identity)], timeout=30).stdout.decode().strip()
        archive = root / "payload.tar"
        with tarfile.open(archive, "w") as tar:
            tar.add(fixture, arcname="payload", recursive=True)
        artifact_dir = root / "artifact"
        artifact_dir.mkdir(mode=0o700)
        encrypted = artifact_dir / "payload.age"
        run(["age", "--recipient", recipient, "--output", str(encrypted), str(archive)])
        public = {key: internal[key] for key in (
            "schema_version", "artifact_id", "host_slug", "app_id", "adapter", "created_at")}
        public.update(payload_sha256=backup.sha256(encrypted), payload_bytes=encrypted.stat().st_size)
        backup.validate_public_manifest(public, 1024 * 1024 * 1024)
        atomic_json(artifact_dir / "manifest.json", public)

        password = root / "passwords" / HOST / f"{APP}.password"
        root_write(password, (uuid.uuid4().hex + "\n").encode())
        config: dict[str, Any] = {
            "schema_version": backup.SCHEMA_VERSION,
            "hosts": [{"host_slug": HOST, "applications": [APP]}],
            "repository_root": str(root / "repository"),
            "password_root": str(root / "passwords"),
            "catalog_root": str(root / "catalog"), "max_artifact_bytes": 1024 * 1024 * 1024,
            "age_identity_file": str(identity), "restore_work_dir": str(restore_run_root / "work"),
            "restore_lock_file": str(root / "locks/restore.lock"),
            "host_lock_root": str(root / "host-locks"),
            "operation_lock_root": str(root / "operations"),
            "evidence_root": str(root / "evidence"),
            "restore_adapter": str(repo / "backup/restore-umami"),
        }
        if restore_run_root.exists() or restore_run_root.is_symlink():
            raise HarnessError("unique restore run root already exists; refusing to reuse it")
        restore_run_root.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        if restore_run_root.parent.is_symlink() or not restore_run_root.parent.is_dir():
            raise HarnessError("restore runtime parent is not a real directory")
        restore_run_root.mkdir(mode=0o700, parents=False)
        restore_run_root.chmod(0o700)
        for directory in (Path(config["restore_work_dir"]), Path(config["restore_lock_file"]).parent,
                          Path(config["host_lock_root"]), Path(config["operation_lock_root"])):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        host_lock = Path(config["host_lock_root"]) / f"operations.host.{HOST}.lock"
        host_lock.touch(mode=0o660)
        host_lock.chmod(0o660)
        (root / "locks/restore.lock").touch(mode=0o600)
        atomic_json(root / "worker.json", config)
        env, repository = backup.restic_env(config, HOST, APP)
        repository.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        run(["restic", "init"], env=env, timeout=60)

        def add_snapshot(point_public: dict[str, Any], path: Path, catalog: dict[str, Any]) -> str:
            output = run(["restic", "backup", str(path), "--json"], env=env, timeout=180).stdout
            snapshot = None
            for line in output.splitlines():
                value = json.loads(line)
                if value.get("message_type") == "summary":
                    snapshot = value.get("snapshot_id")
            if not isinstance(snapshot, str) or not re.fullmatch(r"[a-f0-9]{64}", snapshot):
                raise HarnessError("restic did not return a full snapshot id")
            catalog["recovery_points"].append({
                "snapshot_id": snapshot, "artifact_id": point_public["artifact_id"],
                "host_slug": HOST, "app_id": APP, "created_at": point_public["created_at"],
                "stored_at": backup.now(), "integrity_checked_at": backup.now(),
                "integrity_coverage": "full-repository-data", "payload_bytes": point_public["payload_bytes"],
                "payload_sha256": point_public["payload_sha256"], "policy_version": "production-v1",
                "engine": backup.ENGINE_VERSION, "state": "available", "removed_at": None,
                "repository_observed_at": backup.now(), "run_id": str(uuid.uuid4()),
                "trigger": "manual", "source_id": HOST, "destination_id": "sanctuary-restic",
            })
            return snapshot

        catalog = backup.empty_catalog(HOST, APP)
        snapshot = add_snapshot(public, artifact_dir, catalog)
        backup.write_catalog(config, catalog)
        config_path = root / "worker.json"
        env.update({"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"})
        success = run_cli(repo, root, config_path, snapshot, env)
        evidence_path = root / "evidence" / HOST / APP / f"{artifact_id}.json"
        if success.returncode != 0 or success.stderr or not success.stdout:
            raise HarnessError("native CLI success scenario failed")
        evidence = json.loads(success.stdout)
        persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence != persisted:
            raise HarnessError("CLI stdout and persisted evidence differ")
        if (evidence.get("snapshot_id") != snapshot or evidence.get("artifact_id") != artifact_id
                or evidence.get("payload_sha256") != public["payload_sha256"]
                or evidence.get("restore_status") != "passed"
                or evidence.get("engine_version") != backup.ENGINE_VERSION
                or evidence.get("versions", {}).get("postgres_image") != PG_IMAGE
                or evidence.get("versions", {}).get("application_image") != UMAMI_IMAGE
                or evidence.get("validation", {}).get("schema") != "passed"
                or evidence.get("validation", {}).get("data") != "passed"
                or evidence.get("validation", {}).get("application_health") != "passed"
                or evidence.get("validation", {}).get("encrypted_two_factor_authentication") != "passed"
                or evidence.get("isolation", {}).get("external_effects_blocked") is not True
                or evidence.get("isolation", {}).get("host_ports") != 0
                or evidence.get("cleanup", {}).get("status") != "passed"
                or evidence.get("cleanup", {}).get("containers_removed") is not True
                or evidence.get("cleanup", {}).get("workspace_removed") is not True
                or type(evidence.get("validation", {}).get("public_table_count")) is not int
                or evidence.get("validation", {}).get("public_table_count", 0) < 1
                or evidence.get("validation", {}).get("database_content_marker") != "passed"
                or type(evidence.get("duration_seconds")) is not int
                or evidence.get("duration_seconds", 0) < 1
                or evidence.get("duration_seconds", 14401) > 14400):
            raise HarnessError("native success evidence does not meet the restore contract")
        results.append({"scenario": "success", "status": "PASS", "snapshot_id": snapshot,
                        "artifact_id": artifact_id, "duration_seconds": evidence["duration_seconds"],
                        "evidence": "exact-binding-and-isolation-verified"})

        # Every failure has a distinct evidence root. The last-known-good success
        # evidence and all pre-existing containers must remain unchanged.
        good_evidence_hash = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
        failure_configurations: dict[str, tuple[Path, str, str]] = {}
        failure_expectations: dict[str, tuple[str, int | None, str | None]] = {
            "wrong-key": ("external command failed: age", None, None),
            "corrupt-payload": ("snapshot payload checksum mismatch", None, None),
        }
        failure_events: dict[str, Path] = {}
        wrong_identity = root / "wrong-age-identity.txt"
        run(["age-keygen", "-o", str(wrong_identity)], timeout=30)
        wrong_identity.chmod(0o600)
        wrong_config = {**config, "age_identity_file": str(wrong_identity),
                        "evidence_root": str(root / "fail-evidence/wrong-key")}
        wrong_config_path = root / "wrong-key.json"
        atomic_json(wrong_config_path, wrong_config)
        failure_configurations["wrong-key"] = (wrong_config_path, snapshot, artifact_id)

        for case in ("corrupt-payload", "restore-failure", "application-failure"):
            point, case_artifact = package_point(root, backup, variant=case, recipient=recipient)
            case_snapshot = add_snapshot(point, case_artifact, catalog)
            case_config = {**config, "evidence_root": str(root / "fail-evidence" / case)}
            if case in ("restore-failure", "application-failure"):
                event_path = root / f"{case}.event.json"
                instrumented = instrument_adapter(root, case,
                                                  Path(config["restore_adapter"]), event_path)
                case_config["restore_adapter"] = str(instrumented)
                failure_events[case] = event_path
                failure_expectations[case] = ("external command failed: restore-umami",
                                              1 if case == "restore-failure" else 68,
                                              "pg_restore" if case == "restore-failure"
                                              else "application-health")
            case_config_path = root / f"{case}.json"
            atomic_json(case_config_path, case_config)
            failure_configurations[case] = (case_config_path, case_snapshot, point["artifact_id"])
        backup.write_catalog(config, catalog)

        # A test-only copy of the real adapter injects one failed removal of its
        # own app container. Its EXIT retry must clean both owned containers.
        cleanup_adapter = root / "restore-cleanup-fault"
        docker_shim = root / "cleanup-docker"
        marker_path = root / "cleanup-fault-fired"
        shim = ("#!/bin/bash\nset -euo pipefail\n"
                f"marker={json.dumps(str(marker_path))}\n"
                "if [[ \"$1\" == rm && \"$2\" == --force && \"$3\" =~ ^restore-[0-9]+-[0-9]+-app$ && ! -e \"$marker\" ]]; then\n"
                "  touch \"$marker\"; exit 77\nfi\n"
                f"exec {json.dumps(str(DOCKER))} \"$@\"\n")
        root_write(docker_shim, shim.encode(), 0o700)
        adapter_source = (repo / "backup/restore-umami").read_text(encoding="utf-8")
        if str(DOCKER) not in adapter_source:
            raise HarnessError("fixed Docker adapter contract changed; review shim substitution")
        root_write(cleanup_adapter, adapter_source.replace(str(DOCKER), str(docker_shim)).encode(), 0o700)
        cleanup_event = root / "cleanup-failure.event.json"
        cleanup_wrapper = instrument_adapter(root, "cleanup-failure", cleanup_adapter,
                                             cleanup_event, "restore-cleanup-fault")
        cleanup_config = {**config, "restore_adapter": str(cleanup_adapter),
                          "evidence_root": str(root / "fail-evidence/cleanup-failure")}
        cleanup_config["restore_adapter"] = str(cleanup_wrapper)
        cleanup_config_path = root / "cleanup-failure.json"
        atomic_json(cleanup_config_path, cleanup_config)
        failure_configurations["cleanup-failure"] = (cleanup_config_path, snapshot, artifact_id)
        failure_events["cleanup-failure"] = cleanup_event
        failure_expectations["cleanup-failure"] = (
            "external command failed: restore-cleanup-fault", 70, "cleanup")

        baseline_inventory = inventory()
        work_dir = Path(config["restore_work_dir"])
        adapter_tmp = Path("/run/tuinstra-backup")
        before_adapter_tmp = {p.name for p in adapter_tmp.glob("restore-*")} if adapter_tmp.exists() else set()
        for case, (case_config_path, case_snapshot, case_artifact_id) in failure_configurations.items():
            proc = run_cli(repo, root, case_config_path, case_snapshot, env)
            case_evidence = root / "fail-evidence" / case / HOST / APP / f"{case_artifact_id}.json"
            after_tmp = {p.name for p in adapter_tmp.glob("restore-*")} if adapter_tmp.exists() else set()
            if proc.returncode != 1 or proc.stdout or case_evidence.exists():
                raise HarnessError(f"{case} reported success or persisted false success evidence")
            expected_error, expected_adapter_exit, expected_class = failure_expectations[case]
            cli_error = proc.stderr.decode("utf-8", "replace").strip()
            if cli_error != f"backup operation failed: {expected_error}":
                raise HarnessError(f"{case} failed for an unexpected CLI reason")
            if expected_class is not None:
                event_path = failure_events[case]
                if not event_path.is_file():
                    raise HarnessError(f"{case} did not emit its bounded adapter failure marker")
                event = json.loads(event_path.read_text(encoding="utf-8"))
                if event != {"exit_code": expected_adapter_exit, "failure_class": expected_class}:
                    raise HarnessError(f"{case} adapter failure class or exit code was unexpected")
            if case == "cleanup-failure" and not marker_path.is_file():
                raise HarnessError("cleanup fault-injection marker did not fire")
            if hashlib.sha256(evidence_path.read_bytes()).hexdigest() != good_evidence_hash:
                raise HarnessError(f"{case} changed last-good evidence")
            if inventory() != baseline_inventory:
                raise HarnessError(f"{case} changed container inventory; preserve fixture for diagnosis")
            if list(work_dir.iterdir()) or after_tmp != before_adapter_tmp:
                raise HarnessError(f"{case} left restore workspace or adapter temp state")
            reason = "encrypted-input-rejected" if case in ("wrong-key", "corrupt-payload") else (
                "database-restore-rejected" if case == "restore-failure" else (
                    "application-health-rejected" if case == "application-failure" else "cleanup-retry-rejected"))
            results.append({"scenario": case, "status": "PASS", "exit_code": proc.returncode,
                            "no_success_evidence": True, "last_good_preserved": True,
                            "owned_runtime_clean": True, "failure_class": reason,
                            "expected_cli_error": expected_error,
                            "adapter_exit_code": expected_adapter_exit,
                            "adapter_failure_class": expected_class,
                            "cleanup_fault_marker": marker_path.is_file() if case == "cleanup-failure" else None})

        if inventory() != baseline_inventory:
            raise HarnessError("unexpected container change after cases")
        summary = {"suite": "DEV-33 native isolated restore", "status": "PASS",
                   "scenarios": results, "fixture_path": str(root),
                   "worker_config": str(root / "worker.json"),
                   "measured_capacity_bytes": capacity,
                   "fixture_retained": bool(args.keep_fixture)}
    except Exception as exc:
        retain = True  # Keep failure diagnostics and synthetic fixture for root review.
        message = str(exc) if isinstance(exc, HarnessError) else f"{type(exc).__name__} in fixture harness"
        print(json.dumps({"suite": "DEV-33 native isolated restore", "status": "FAIL",
                          "reason": message, "fixture_path": str(root),
                          "fixture_retained": True}, sort_keys=True))
        return 1
    finally:
        cleanup_errors = []
        for name in reversed(created_containers):
            try:
                remove_own_container(name)
            except HarnessError:
                cleanup_errors.append(name)
        if (restore_run_root.parent == Path("/run/tuinstra-backup")
                and re.fullmatch(rf"dev33-native-{run_id}", restore_run_root.name)):
            if restore_run_root.is_symlink():
                cleanup_errors.append(restore_run_root.name)
            elif restore_run_root.exists():
                try:
                    shutil.rmtree(restore_run_root)
                except OSError:
                    cleanup_errors.append(restore_run_root.name)
        if cleanup_errors:
            retain = True
            print(json.dumps({"suite": "DEV-33 fixture cleanup", "status": "BLOCKED",
                              "owned_resources_remaining": cleanup_errors,
                              "fixture_path": str(root), "action": "manual exact-name cleanup only"}, sort_keys=True))
        if not retain:
            # This is constrained to this invocation's new mkdtemp child. It
            # never touches the pre-existing /tmp/dev33-20261005 fixture.
            if root.parent == Path("/tmp") and root.name.startswith("dev33-native-"):
                shutil.rmtree(root)

    if cleanup_errors:
        return 1
    if summary is not None:
        print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessError as exc:
        print(json.dumps({"suite": "DEV-33 native isolated restore", "status": "BLOCKED",
                          "reason": str(exc)}, sort_keys=True))
        raise SystemExit(2)
