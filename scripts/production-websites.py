#!/usr/bin/env python3
"""DEV-26: validate and atomically reload only the four existing public routes.

Run on VPS01 as the existing administrator/root, never as a delegated endpoint.
No image, credential, database, firewall, DNS or unrelated route is modified.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile

ROOT = Path("/var/www/_platform/caddy")
SITES = ROOT / "sites"
CANDIDATE = ROOT / "DEV-26-candidate"
BACKUPS = ROOT / "DEV-26-backups"
PRODUCER = Path("/etc/tuinstra-backup/producer.json")
NAMES = tuple(x + ".caddy" for x in ("wodiq-site", "site-openairco", "site-marcel", "site-tuinstra"))


def secure(path, directory=False):
    for parent in [path, *path.parents]:
        info = parent.lstat()
        if parent.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("contract ownership or permissions are unsafe")
    if not (path.is_dir() if directory else path.is_file()):
        raise RuntimeError("contract type is invalid")
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(command):
    result = subprocess.run(command, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Caddy operation failed; inspect the retained recovery contract")
    return result.stdout


def atomic(path, data, mode=0o644, gid=None):
    fd, temporary = tempfile.mkstemp(prefix=".DEV-26-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            if gid is not None:
                os.fchown(stream.fileno(), -1, gid)
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(folder, expected=None, check=False):
    secure(ROOT, directory=True)
    secure(SITES, directory=True)
    secure(folder, directory=True)
    before = {name: secure(SITES / name).read_bytes() for name in NAMES}
    after = {name: secure(folder / name).read_bytes() for name in NAMES}
    if any(not data or len(data) > 32768 for data in after.values()):
        raise RuntimeError("candidate route is empty or too large")
    if expected is not None and {n: digest(b) for n, b in before.items()} != expected:
        raise RuntimeError("routes changed since this snapshot; refusing a stale rollback")
    ids = run(["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=caddy",
               "--filter", "label=com.docker.compose.service=caddy"]).decode().split()
    if len(ids) != 1:
        raise RuntimeError("expected exactly one running Caddy edge")
    container = ids[0]
    image = json.loads(run(["docker", "inspect", container]))[0]["Config"]["Image"]
    if not re.fullmatch(r"(?:docker\.io/library/)?caddy:[^\s]+@sha256:[0-9a-f]{64}", image):
        raise RuntimeError("active Caddy image must be immutable")
    with tempfile.TemporaryDirectory(prefix=".DEV-26-check-", dir=ROOT) as directory:
        stage = Path(directory)
        (stage / "sites").mkdir()
        shutil.copy2(secure(ROOT / "Caddyfile"), stage / "Caddyfile")
        for path in SITES.glob("*.caddy"):
            shutil.copy2(secure(path), stage / "sites" / path.name)
        for name, data in after.items():
            (stage / "sites" / name).write_bytes(data)
        run(["docker", "run", "--rm", "--pull=never", "--network=none", "--read-only",
             "--cap-drop=ALL", "--cap-add=NET_BIND_SERVICE", "--security-opt=no-new-privileges:true",
             "--tmpfs", "/data:rw,noexec,nosuid,size=16m",
             "--tmpfs", "/config:rw,noexec,nosuid,size=16m",
             "--mount", f"type=bind,source={stage},target=/etc/caddy,readonly",
             image, "caddy", "validate", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"])
    changed = [name for name in NAMES if before[name] != after[name]]
    if check or not changed:
        return {"checked": True, "changed": changed, "applied": False, "image": image}
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    secure(BACKUPS, directory=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = BACKUPS / stamp
    backup.mkdir(mode=0o700)
    for name, data in before.items():
        atomic(backup / name, data)
    manifest = {"schema": "DEV-26.ingress.v1", "before": {n: digest(b) for n, b in before.items()},
                "after": {n: digest(b) for n, b in after.items()}, "image": image}
    atomic(backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode(), 0o600)
    reload_command = ["docker", "exec", container, "caddy", "reload", "--config",
                      "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
    try:
        for name in changed:
            atomic(SITES / name, after[name])
        run(reload_command)
    except Exception:
        for name in changed:
            atomic(SITES / name, before[name])
        run(reload_command)
        raise RuntimeError("candidate rejected; original routes restored and reloaded") from None
    return {"applied": True, "changed": changed, "backup": str(backup), **manifest}


def extend_backup_profile():
    """Preserve the installed producer, adding only explicit web recovery inputs."""
    path = PRODUCER
    secure(path)
    original_info = path.stat()
    original = path.read_bytes()
    profile = json.loads(original)
    if profile.get("host_slug") != "tuinstra-prod-01":
        raise RuntimeError("backup producer host is invalid")
    apps = [a for a in profile["applications"] if a.get("app_id") == "umami"]
    if len(apps) != 1 or apps[0].get("adapter") != "postgres-compose-v1" or not apps[0].get("enabled"):
        raise RuntimeError("existing enabled Umami producer is required")
    inputs = [("host-caddyfile", ROOT / "Caddyfile"), ("host-caddy-compose", ROOT / "compose.yml"),
              ("host-deploy-helper", Path("/usr/local/sbin/tuinstra-compose-deploy")),
              ("host-deploy-shell", Path("/usr/local/sbin/tuinstra-deploy-shell"))]
    for name in NAMES:
        app = name.removesuffix(".caddy")
        inputs.extend([(app + "-ingress", SITES / name),
                       (app + "-compose", Path("/var/www") / app / "compose.yml"),
                       (app + "-registry", Path("/etc/tuinstra") / app / "docker-config/config.json")])
    existing = apps[0]["included_files"]
    if len({item["name"] for item in existing}) != len(existing):
        raise RuntimeError("existing producer input names conflict")
    by_name = {item["name"]: item for item in existing}
    added = []
    for name, source in inputs:
        secure(source)  # Metadata only; secrets remain inside the encrypted exporter.
        item = {"name": name, "path": str(source)}
        if name in by_name and by_name[name] != item:
            raise RuntimeError("existing producer input has a conflicting path")
        if name not in by_name:
            added.append(item)
    if not added:
        return {"extended": 0, "app": "umami"}
    existing.extend(added)
    BACKUPS.mkdir(mode=0o700, exist_ok=True)
    secure(BACKUPS, directory=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = BACKUPS / ("producer-" + stamp + ".json")
    atomic(backup, original, 0o600)
    updated = (json.dumps(profile, indent=2) + "\n").encode()
    atomic(path, updated, original_info.st_mode & 0o777, gid=original_info.st_gid)
    return {"extended": len(added), "app": "umami", "backup": str(backup),
            "profile_sha256": digest(updated)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "apply", "rollback", "backup-profile"])
    parser.add_argument("snapshot", nargs="?")
    args = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname() != "tuinstra-prod-01":
        raise RuntimeError("requires the existing administrator on tuinstra-prod-01")
    if (args.action == "rollback") != (args.snapshot is not None):
        raise RuntimeError("only rollback takes a snapshot identity")
    lock_path = Path("/run/lock/tuinstra/operations.host.tuinstra-prod-01.lock")
    info = lock_path.lstat()
    if lock_path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o777 != 0o660:
        raise RuntimeError("existing host operation lock is unavailable")
    with lock_path.open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for app in ("umami", "status"):
            marker = Path("/var/lib/tuinstra-production-restore/maintenance") / app
            if marker.exists() or marker.is_symlink():
                raise RuntimeError("production restore maintenance is active")
        if args.action == "backup-profile":
            print(json.dumps(extend_backup_profile()))
            return
        folder, expected = CANDIDATE, None
        if args.action == "rollback":
            if not re.fullmatch(r"[0-9]{8}T[0-9]{12}Z", args.snapshot):
                raise RuntimeError("invalid snapshot identity")
            folder = BACKUPS / args.snapshot
            manifest = json.loads(secure(folder / "manifest.json").read_text())
            if manifest.get("schema") != "DEV-26.ingress.v1" or set(manifest["after"]) != set(NAMES):
                raise RuntimeError("snapshot manifest is invalid")
            if {n: digest(secure(folder / n).read_bytes()) for n in NAMES} != manifest["before"]:
                raise RuntimeError("snapshot checksum mismatch")
            expected = manifest["after"]
        print(json.dumps(apply(folder, expected, check=args.action == "check")))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
