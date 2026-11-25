#!/usr/bin/env python3
"""DEV-26: validate and atomically reload only the three existing public routes.

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
import signal
import socket
import subprocess
import tempfile

ROOT = Path("/var/www/_platform/caddy")
SITES = ROOT / "sites"
CANDIDATE = ROOT / "DEV-26-candidate"
BACKUPS = ROOT / "DEV-26-backups"
NAMES = tuple(x + ".caddy" for x in ("wodiq-site", "site-marcel", "site-tuinstra"))


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "apply", "rollback"])
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


def release(app, source, image_digest):
    from production_website_release import HOSTS, WebsiteRelease, deploy_website

    if os.geteuid() != 0 or socket.gethostname() != "tuinstra-prod-01" or app not in HOSTS:
        raise RuntimeError("website release is unavailable on this host")
    lock_path = Path("/run/lock/tuinstra/operations.host.tuinstra-prod-01.lock")
    info = lock_path.lstat()
    if lock_path.is_symlink() or not lock_path.is_file() or info.st_uid != 0 or info.st_mode & 0o777 != 0o660:
        raise RuntimeError("existing host operation lock is unavailable")
    with lock_path.open("r") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for name in (app, "umami", "status"):
            marker = Path("/var/lib/tuinstra-production-restore/maintenance") / name
            if os.path.lexists(marker):
                raise RuntimeError("production restore maintenance is active")
        signal.signal(signal.SIGHUP, interrupted)
        signal.signal(signal.SIGTERM, interrupted)
        deploy_website(WebsiteRelease(app, source, image_digest, SITES, secure, atomic))


def interrupted(_signal, _frame):
    raise RuntimeError("website deployment interrupted")


if __name__ == "__main__":
    import sys

    try:
        if sys.argv[1:2] == ["release"]:
            if len(sys.argv) != 5:
                raise RuntimeError("release requires an application, commit and digest")
            release(*sys.argv[2:])
        else:
            main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"error": str(error)}))
        raise SystemExit(1)
