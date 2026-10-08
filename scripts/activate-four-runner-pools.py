#!/usr/bin/env python3
"""Drained, root-only activation of DEV-50 on the existing Sanctuary runner host.

Stage this script and the two reviewed Python binaries in a root-owned directory.
Run --preflight, then --activate with interactive sudo. This intentionally does
not provision packages, credentials, images, networking, or GitHub settings.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import Callable


MANAGER_SHA256 = "1d141705f975efafcdc85c8941f3879c395b4988bc1373dd4750b995935968e7"
HELPER_SHA256 = "fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613"
UNIT_SHA256 = "c99fdf23b9c50971be678565db54e85d5227040ff1108ccb3622a9c2ae4d806d"
SYSTEMD_UNIT_OBJECT = "/org/freedesktop/systemd1/unit/ci_2drunner_2dmanager_2eservice"
SOURCE_FILES = {"manager": "ci_runner_manager.py", "helper": "ci_runner_host_helper.py"}
SERVICE = "ci-runner-manager.service"
SOCKET = "ci-runner-host-helper.socket"
LIBVIRT_URI = "qemu:///system"
DOMAIN_PREFIX = "sanctuary-ci-"
ROOT_SOURCE = "/dev/mapper/ubuntu--vg-ubuntu--lv"
GIB = 1024**3
POOL_VALUES = {
    "pool_mode": "four", "medium_runner_label": "trusted-medium",
    "medium_runner_vcpus": 2, "medium_runner_memory_mib": 3072,
    "max_heavy": 2, "max_medium": 2,
    "heavy_disk_reservation_gib": 24, "medium_disk_reservation_gib": 4,
    "storage_mode": "bounded-loop",
}
POOL_LINES = (
    '\npool_mode = "four"\nmedium_runner_label = "trusted-medium"\n'
    'medium_runner_vcpus = 2\nmedium_runner_memory_mib = 3072\n'
    'max_heavy = 2\nmax_medium = 2\n'
    'heavy_disk_reservation_gib = 24\nmedium_disk_reservation_gib = 4\n'
    'storage_mode = "bounded-loop"\n'
)
DRAIN_DROPIN = (
    "[Service]\nType=oneshot\nExecStart=\n"
    "ExecStart=/usr/local/bin/ci-runner-manager --config "
    "/etc/ci-runner/manager.toml reconcile\nRestart=no\n"
)


class ActivationError(RuntimeError):
    pass


class CanaryIndeterminate(ActivationError):
    """The transient root canary may still be running; never restart admission."""


@dataclass(frozen=True)
class Layout:
    config: Path = Path("/etc/ci-runner/manager.toml")
    manager: Path = Path("/usr/local/bin/ci-runner-manager")
    helper: Path = Path("/usr/local/libexec/ci-runner-host-helper")
    unit: Path = Path("/etc/systemd/system/ci-runner-manager.service")
    overlay: Path = Path("/var/lib/ci-runner/overlay")
    state: Path = Path("/var/lib/ci-runner-manager/state")
    lock: Path = Path("/run/lock/ci-runner-four-pool-activation.lock")
    helper_lock: Path = Path("/run/lock/ci-runner-host-helper.lock")
    dropin: Path = Path(
        "/run/systemd/system/ci-runner-manager.service.d/dev50-four-pool-drain.conf"
    )
    backup_root: Path = Path("/var/backups/ci-runner-four-pool")


HOST = Layout()
Command = Callable[..., subprocess.CompletedProcess[str]]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(path: Path, *, owner: int = 0) -> tuple[bytes, os.stat_result]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_uid != owner or before.st_mode & 0o022:
        raise ActivationError(f"unsafe owner, mode, or file type: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ActivationError(f"file changed while opening: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read()
    finally:
        os.close(fd)
    return data, opened


def checked_directory(path: Path, *, owner: int = 0) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise ActivationError(f"unsafe directory: {path}")


def state_owner() -> int:
    return pwd.getpwnam("ci-runner-manager").pw_uid


def run(args: list[str], *, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActivationError(f"{Path(args[0]).name} unavailable or timed out") from exc
    if check and result.returncode:
        raise ActivationError(f"{Path(args[0]).name} failed with exit {result.returncode}")
    return result


def validate_legacy_config(data: bytes) -> tuple[dict, bytes]:
    try:
        text = data.decode("utf-8")
        old = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ActivationError("installed manager configuration is invalid TOML") from exc
    resizing = old.get("pool_mode") == "four"
    exact = {
        "max_concurrency": 4 if resizing else 1, "runner_vcpus": 4, "runner_memory_mib": 6144,
        "host_memory_reserve_mib": 4096, "min_free_disk_gib": 60,
        "max_lease_seconds": 7200, "runner_label": "trusted-heavy",
        "runner_group_id": 1, "allowed_owner": "Tuinstra-DEV",
        "state_dir": "/var/lib/ci-runner-manager/state",
        "helper_socket": "/run/ci-runner-host-helper.sock",
        "overlay_root": "/var/lib/ci-runner/overlay",
        "github_token_file": "/run/credentials/ci-runner-manager.service/github_token",
        "github_api_url": "https://api.github.com",
    }
    for key, expected in exact.items():
        if key not in old or type(old[key]) is not type(expected) or old[key] != expected:
            raise ActivationError(f"installed manager policy differs at {key}")
    if resizing:
        previous = {**POOL_VALUES, "heavy_disk_reservation_gib": 12}
        for key, expected in previous.items():
            if type(old.get(key)) is not type(expected) or old.get(key) != expected:
                raise ActivationError(f"installed four-pool policy differs at {key}")
    else:
        for key in ("pool_mode", *POOL_VALUES):
            if key in old:
                raise ActivationError("unsupported partial four-pool policy")
    repositories = old.get("repositories")
    if not isinstance(repositories, list) or not repositories or \
            any(not isinstance(repo, str) or not repo.startswith("Tuinstra-DEV/")
                for repo in repositories) or len(repositories) != len(set(repositories)) or \
            "Tuinstra-DEV/wodiq-platform" not in repositories:
        raise ActivationError("installed repository allowlist is not the reviewed owner set")
    if resizing:
        matches = list(re.finditer(r"(?m)^heavy_disk_reservation_gib = 12$", text))
        if len(matches) != 1:
            raise ActivationError("heavy disk reservation line has an unsupported format")
        candidate = text[:matches[0].start()] + "heavy_disk_reservation_gib = 24" + text[matches[0].end():]
        if tomllib.loads(candidate) != {**old, "heavy_disk_reservation_gib": 24}:
            raise ActivationError("heavy disk transformation is not isolated")
        return old, candidate.encode("utf-8")
    matches = list(re.finditer(r"(?m)^max_concurrency = 1$", text))
    if len(matches) != 1:
        raise ActivationError("max_concurrency line has an unsupported format")
    candidate = (text[:matches[0].start()] + "max_concurrency = 4" +
                 text[matches[0].end():]).rstrip("\n") + "\n" + POOL_LINES
    updated = tomllib.loads(candidate)
    if updated != {**old, "max_concurrency": 4, **POOL_VALUES}:
        raise ActivationError("manager configuration transformation is not isolated")
    return old, candidate.encode("utf-8")


def source_payloads(stage: Path, layout: Layout = HOST) -> dict[str, bytes]:
    checked_directory(stage)
    result = {}
    for key, filename in SOURCE_FILES.items():
        data, _ = read_regular(stage / filename)
        expected = MANAGER_SHA256 if key == "manager" else HELPER_SHA256
        if digest(data) != expected:
            raise ActivationError(f"reviewed {key} SHA-256 does not match staged file")
        result[key] = data
    for path in (layout.config.parent, layout.manager.parent, layout.helper.parent):
        checked_directory(path)
    return result


def installed_payloads(layout: Layout = HOST) -> tuple[dict[str, bytes], dict[str, os.stat_result]]:
    content = {}
    metadata = {}
    for key, path in (("config", layout.config), ("manager", layout.manager),
                      ("helper", layout.helper)):
        content[key], metadata[key] = read_regular(path)
    validate_legacy_config(content["config"])
    return content, metadata


def verify_base_unit(layout: Layout = HOST, command: Command = run) -> None:
    unit, _ = read_regular(layout.unit)
    if digest(unit) != UNIT_SHA256:
        raise ActivationError("installed manager unit differs from the reviewed unit")
    fields = {
        "FragmentPath": str(layout.unit), "User": "ci-runner-manager",
        "Group": "ci-runner-manager", "Type": "simple", "Restart": "always",
    }
    for field, expected in fields.items():
        result = command(["systemctl", "show", SERVICE, f"--property={field}", "--value"])
        if result.stdout.strip() != expected:
            raise ActivationError(f"effective manager unit differs at {field}")
    start = command(["systemctl", "show", SERVICE, "--property=ExecStart", "--value"]).stdout
    if start.count("argv[]=") != 1 or \
            "argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30" not in start:
        raise ActivationError("effective manager command differs from the reviewed daemon")
    verify_credential_binding(command)


def verify_credential_binding(command: Command = run) -> None:
    response = command([
        "busctl", "--json=short", "get-property", "org.freedesktop.systemd1",
        SYSTEMD_UNIT_OBJECT, "org.freedesktop.systemd1.Service", "LoadCredential",
    ])
    try:
        document = json.loads(response.stdout)
    except json.JSONDecodeError as exc:
        raise ActivationError("effective manager credential binding is unreadable") from exc
    if document != {"type": "a(ss)",
                    "data": [["github_token", "/etc/ci-runner/github.token"]]}:
        raise ActivationError("effective manager credential binding differs")


def verify_host(layout: Layout = HOST, command: Command = run) -> None:
    for name in ("systemctl", "busctl", "systemd-run", "journalctl", "virsh", "findmnt", "fallocate",
                 "mkfs.ext4", "losetup", "blockdev", "systemd-mount", "nsenter",
                 "fstrim", "udevadm", "python3"):
        if shutil.which(name) is None:
            raise ActivationError(f"required host tool is missing: {name}")
    checked_directory(layout.overlay)
    checked_directory(layout.state, owner=state_owner())
    if layout.overlay.stat().st_dev != Path("/").stat().st_dev:
        raise ActivationError("runner overlay is not on the root NVMe filesystem")
    source = command(["findmnt", "--noheadings", "--output", "SOURCE",
                      "--target", str(layout.overlay)]).stdout.strip()
    if source != ROOT_SOURCE:
        raise ActivationError("runner overlay root source differs from reviewed NVMe")
    if shutil.disk_usage(layout.overlay).free < 86 * GIB:
        raise ActivationError("root NVMe lacks the 24-GiB canary and 62-GiB floor")


def assert_drained(layout: Layout = HOST, command: Command = run) -> None:
    domains = command(["virsh", "--connect", LIBVIRT_URI,
                       "list", "--all", "--name"]).stdout.splitlines()
    if any(domain.startswith(DOMAIN_PREFIX) for domain in domains):
        raise ActivationError("runner VM remains; waiting for natural completion")
    for path in (layout.state, layout.overlay):
        checked_directory(path, owner=0 if path == layout.overlay else state_owner())
        entries = list(path.iterdir())
        if path == layout.overlay and entries:
            raise ActivationError("runner overlay retains a lease or orphan")
        if path == layout.state and any(
                item.name.startswith(("lease-", "cleanup-")) or item.name.endswith(".tmp")
                for item in entries):
            raise ActivationError("runner state retains a lease or cleanup obligation")


def atomic_write(path: Path, data: bytes, *, uid: int, gid: int, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.dev50-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup_files(layout: Layout, original: dict[str, bytes],
                 metadata: dict[str, os.stat_result], replacement: dict[str, bytes]) -> Path:
    checked_directory(layout.backup_root.parent)
    layout.backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    checked_directory(layout.backup_root)
    identity = datetime.now(timezone.utc).strftime("dev50-%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
    backup = layout.backup_root / identity
    backup.mkdir(mode=0o700)
    manifest = {}
    for key in ("config", "manager", "helper"):
        info = metadata[key]
        atomic_write(backup / key, original[key], uid=0, gid=0, mode=0o600)
        manifest[key] = {"old_sha256": digest(original[key]),
                         "new_sha256": digest(replacement[key]),
                         "uid": info.st_uid, "gid": info.st_gid,
                         "mode": stat.S_IMODE(info.st_mode)}
    atomic_write(backup / "manifest.json", json.dumps(manifest, sort_keys=True).encode(),
                 uid=0, gid=0, mode=0o600)
    return backup


def restore_files(layout: Layout, backup: Path, *, allow_partial: bool) -> None:
    if backup.parent != layout.backup_root or not re.fullmatch(r"dev50-\d{8}T\d{6}Z-[a-f0-9]{8}", backup.name):
        raise ActivationError("backup identifier is invalid")
    checked_directory(backup)
    manifest_data, _ = read_regular(backup / "manifest.json")
    manifest = json.loads(manifest_data)
    paths = {"config": layout.config, "manager": layout.manager, "helper": layout.helper}
    recovered = {}
    for key, path in paths.items():
        recovered[key], _ = read_regular(backup / key)
        current, _ = read_regular(path)
        entry = manifest[key]
        allowed = {entry["old_sha256"], entry["new_sha256"]} if allow_partial \
            else {entry["new_sha256"]}
        if digest(recovered[key]) != entry["old_sha256"] or digest(current) not in allowed:
            raise ActivationError(f"backup or current {key} does not match activation record")
    for key in ("manager", "helper", "config"):
        entry = manifest[key]
        atomic_write(paths[key], recovered[key], uid=entry["uid"], gid=entry["gid"],
                     mode=entry["mode"])


def activate_dropin(layout: Layout, command: Command = run) -> tuple[int, int]:
    layout.dropin.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    checked_directory(layout.dropin.parent)
    fd = os.open(layout.dropin, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    info = os.fstat(fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(DRAIN_DROPIN.encode())
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        layout.dropin.unlink(missing_ok=True)
        raise
    try:
        command(["systemctl", "daemon-reload"])
    except Exception:
        if layout.dropin.lstat().st_ino == info.st_ino:
            layout.dropin.unlink()
        raise
    return info.st_dev, info.st_ino


def remove_dropin(layout: Layout, identity: tuple[int, int], command: Command = run) -> None:
    current, info = read_regular(layout.dropin)
    if (info.st_dev, info.st_ino) != identity or current != DRAIN_DROPIN.encode():
        raise ActivationError("runtime drain drop-in changed; refusing to remove it")
    layout.dropin.unlink()
    command(["systemctl", "daemon-reload"])


def service_active(name: str, command: Command = run) -> bool:
    return command(["systemctl", "is-active", "--quiet", name], check=False).returncode == 0


def assert_no_helper_instances(command: Command = run) -> None:
    result = command([
        "systemctl", "list-units", "--all", "--type=service",
        "--state=activating,running,reloading,deactivating", "--plain", "--no-legend",
        "ci-runner-host-helper@*.service",
    ])
    if result.stdout.strip():
        raise ActivationError("runner host helper instance remains active")


def drain_existing_jobs(layout: Layout, seconds: int, command: Command = run,
                        pause: Callable[[float], None] = time.sleep) -> tuple[int, int]:
    if not service_active(SERVICE, command):
        raise ActivationError("existing runner manager is not active")
    command(["systemctl", "stop", SERVICE])
    if service_active(SERVICE, command):
        raise ActivationError("runner manager did not stop")
    identity = activate_dropin(layout, command)
    try:
        if command(["systemctl", "show", SERVICE, "--property=Type", "--value"]).stdout.strip() != "oneshot" or \
                command(["systemctl", "show", SERVICE, "--property=Restart", "--value"]).stdout.strip() != "no":
            raise ActivationError("runtime drain unit did not become reconcile-only")
        effective = command(["systemctl", "show", SERVICE, "--property=ExecStart", "--value"]).stdout
        if effective.count("argv[]=") != 1 or \
                "argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml reconcile" not in effective:
            raise ActivationError("runtime drain command does not match reviewed reconcile-only form")
        verify_credential_binding(command)
        deadline = time.monotonic() + seconds
        while True:
            command(["systemctl", "start", SERVICE], timeout=180)
            try:
                assert_drained(layout, command)
                return identity
            except ActivationError as exc:
                if time.monotonic() >= deadline:
                    raise ActivationError("runner drain deadline reached without destroying jobs") from exc
                pause(min(15.0, max(0.0, deadline - time.monotonic())))
    except Exception:
        remove_dropin(layout, identity, command)
        raise


CANARY_CODE = r'''import fcntl, importlib.util, os, pathlib, shutil, subprocess, sys
from importlib.machinery import SourceFileLoader
path = "/usr/local/libexec/ci-runner-host-helper"
loader = SourceFileLoader("ci_runner_host_helper_canary", path)
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)
lease = sys.argv[1]
def error_code(exc):
    message = str(exc)
    if message == "runner mount namespaces disagree":
        return "mount_namespace_mismatch"
    if message == "runner mount did not become visible in both namespaces":
        return "mount_not_visible"
    if message in ("runner mount state cannot be verified",
                   "local runner mount state cannot be verified"):
        return "mount_query_failed"
    if message in ("runner backing file is not fully allocated",
                   "filesystem creation released backing reservation",
                   "runner backing reservation changed before guest creation"):
        return "backing_allocation"
    if message in ("runner loop backing file does not match lease",
                   "runner loop size does not match lease"):
        return "loop_binding"
    if message in ("runner loop discard could not be disabled",
                   "runner loop discard is enabled", "canary discard limit changed"):
        return "discard_limit"
    if message == "canary discard guard failed":
        return "trim_guard"
    return "unknown"
stage = "initial"
reported = False
try:
    with helper.HELPER_LOCK.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stage = "prepare"
        directory = helper.lease_dir(lease)
        if directory.exists():
            raise RuntimeError("canary lease path is occupied")
        directory.mkdir(mode=0o700)
        _uid, gid = helper.qemu_identity()
        helper.set_qemu_access(directory, 0, gid, 0o710)
        try:
            stage = "create"
            work = helper.create_bounded_storage(lease, "heavy", gid)
            backing = directory / "bounded.img"
            stage = "verify"
            if not helper.verify_bounded_mount(backing, work):
                raise RuntimeError("canary mount was not verified")
            blocks = backing.stat().st_blocks
            stage = "trim"
            trim = subprocess.run(["fstrim", str(work)], capture_output=True, timeout=30)
            if trim.returncode == 0 or backing.stat().st_blocks != blocks:
                raise RuntimeError("canary discard guard failed")
            stage = "discard"
            if helper.loop_discard_limit(helper.host_mount_source(work)) != 0:
                raise RuntimeError("canary discard limit changed")
        finally:
            try:
                helper.remove_bounded_storage(lease)
            except Exception as cleanup_error:
                print("DEV50_CANARY stage=cleanup error=" + error_code(cleanup_error),
                      file=sys.stderr, flush=True)
                reported = True
                raise
        stage = "cleanup"
        if directory.exists():
            raise RuntimeError("canary storage remains after cleanup")
except Exception as exc:
    if not reported:
        print("DEV50_CANARY stage=" + stage + " error=" + error_code(exc),
              file=sys.stderr, flush=True)
    raise
print("DEV50_CANARY stage=complete error=none", file=sys.stderr, flush=True)
'''


CANARY_MARKER = re.compile(
    r"^DEV50_CANARY stage=(initial|prepare|create|verify|trim|discard|cleanup|complete) "
    r"error=(none|mount_namespace_mismatch|mount_not_visible|mount_query_failed|backing_allocation|"
    r"loop_binding|discard_limit|trim_guard|unknown)$", re.MULTILINE,
)


def stop_and_verify_canary(unit: str, command: Command = run) -> None:
    service = f"{unit}.service"
    try:
        command(["systemctl", "stop", service], check=False, timeout=30)
        status = command([
            "systemctl", "show", service, "--property=LoadState",
            "--property=ActiveState", "--property=MainPID",
            "--property=ControlPID",
        ], check=False, timeout=30)
    except Exception as exc:
        raise CanaryIndeterminate(f"canary unit={service} stop_status=unknown") from exc
    fields = {}
    for line in status.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in fields:
            raise CanaryIndeterminate(f"canary unit={service} stop_status=unknown")
        fields[key] = value
    if status.returncode != 0 or set(fields) != {"LoadState", "ActiveState", "MainPID", "ControlPID"} or \
            fields["LoadState"] not in {"loaded", "not-found"} or \
            fields["ActiveState"] not in {"inactive", "failed"} or \
            fields["MainPID"] != "0" or fields["ControlPID"] != "0":
        raise CanaryIndeterminate(f"canary unit={service} stop_status=unverified")


def storage_canary(command: Command = run) -> None:
    lease = "dev50-canary-" + secrets.token_hex(6)
    unit = "ci-runner-dev50-canary-" + secrets.token_hex(6)
    args = [
        "systemd-run", "--quiet", "--wait", "--collect", f"--unit={unit}",
        "--property=NoNewPrivileges=yes", "--property=ProtectSystem=strict",
        "--property=ProtectHome=yes", "--property=PrivateTmp=yes",
        "--property=RuntimeMaxSec=240",
        "--property=ReadWritePaths=/var/lib/ci-runner/overlay /run/lock",
        "--property=RestrictAddressFamilies=AF_UNIX",
        "--property=StandardOutput=journal", "--property=StandardError=journal", "--",
        "/usr/bin/python3", "-c", CANARY_CODE, lease,
    ]
    try:
        result = command(args, check=False, timeout=300)
    except Exception as exc:
        stop_and_verify_canary(unit, command)
        raise ActivationError(f"canary unit={unit} exit=unknown stage=unknown error=launcher_failed") from exc
    if result.returncode:
        stop_and_verify_canary(unit, command)
        try:
            journal = command(["journalctl", f"--unit={unit}", "--output=cat",
                               "--no-pager", "--lines=120"], check=False)
        except Exception:
            journal = None
        markers = CANARY_MARKER.findall(journal.stdout) if journal is not None \
            and journal.returncode == 0 else []
        stage, error = markers[-1] if markers else ("unknown", "unknown")
        raise ActivationError(
            f"canary unit={unit} exit={result.returncode} stage={stage} error={error}"
        )


def lock_file(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        os.close(fd)
        raise ActivationError("unsafe activation or helper lock")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise ActivationError("runner host helper is busy") from exc
    return fd


def preflight(stage: Path, layout: Layout = HOST, command: Command = run) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, os.stat_result]]:
    sources = source_payloads(stage, layout)
    original, metadata = installed_payloads(layout)
    verify_base_unit(layout, command)
    verify_host(layout, command)
    _old, new_config = validate_legacy_config(original["config"])
    replacement = {**sources, "config": new_config}
    return replacement, original, metadata


def activate(stage: Path, seconds: int, layout: Layout = HOST,
             command: Command = run) -> Path:
    replacement, original, metadata = preflight(stage, layout, command)
    lock = lock_file(layout.lock)
    dropin_identity = None
    backup = None
    try:
        dropin_identity = drain_existing_jobs(layout, seconds, command)
        command(["systemctl", "stop", SERVICE])
        command(["systemctl", "stop", SOCKET])
        if service_active(SERVICE, command) or service_active(SOCKET, command):
            raise ActivationError("runner admission services did not stop")
        assert_no_helper_instances(command)
        helper_lock = lock_file(layout.helper_lock)
        try:
            assert_drained(layout, command)
            current, _ = installed_payloads(layout)
            if current != original:
                raise ActivationError("installed files changed during drain")
            backup = backup_files(layout, original, metadata, replacement)
            for key, path in (("manager", layout.manager), ("helper", layout.helper),
                              ("config", layout.config)):
                info = metadata[key]
                atomic_write(path, replacement[key], uid=info.st_uid, gid=info.st_gid,
                             mode=stat.S_IMODE(info.st_mode))
                if digest(read_regular(path)[0]) != digest(replacement[key]):
                    raise ActivationError("installed runner file failed readback")
        finally:
            os.close(helper_lock)
        storage_canary(command)
        assert_drained(layout, command)
        remove_dropin(layout, dropin_identity, command)
        dropin_identity = None
        verify_base_unit(layout, command)
        command(["systemctl", "start", SOCKET])
        command(["systemctl", "start", SERVICE])
        if not service_active(SOCKET, command) or not service_active(SERVICE, command):
            raise ActivationError("runner services did not become active")
        return backup
    except Exception as original_error:
        safe_reason = str(original_error) if isinstance(original_error, ActivationError) \
            else type(original_error).__name__
        if isinstance(original_error, CanaryIndeterminate):
            admission_stopped = True
            for service in (SERVICE, SOCKET):
                try:
                    command(["systemctl", "stop", service], check=False)
                    if service_active(service, command):
                        admission_stopped = False
                except Exception:
                    admission_stopped = False
            state = "admission remains stopped" if admission_stopped \
                else "admission stop could not be verified"
            raise CanaryIndeterminate(
                f"activation indeterminate; {state}; backup={backup or 'none'}; "
                f"failure={safe_reason}"
            ) from original_error
        try:
            command(["systemctl", "stop", SERVICE], check=False)
            if backup is not None:
                command(["systemctl", "stop", SOCKET], check=False)
                assert_drained(layout, command)
                restore_files(layout, backup, allow_partial=True)
            if dropin_identity is not None:
                remove_dropin(layout, dropin_identity, command)
            verify_base_unit(layout, command)
            command(["systemctl", "start", SOCKET])
            command(["systemctl", "start", SERVICE])
            if not service_active(SERVICE, command):
                raise ActivationError("original runner manager did not restart")
        except Exception as recovery_error:
            raise ActivationError(
                "activation failed and safe automatic rollback could not be verified; "
                f"runner admission remains stopped; backup={backup or 'none'}; "
                f"failure={safe_reason}; recovery={type(recovery_error).__name__}"
            ) from original_error
        raise ActivationError(f"activation aborted; original runner policy restored; "
                              f"failure={safe_reason}") from original_error
    finally:
        os.close(lock)


def rollback(backup_id: str, layout: Layout = HOST, command: Command = run) -> None:
    if not re.fullmatch(r"dev50-\d{8}T\d{6}Z-[a-f0-9]{8}", backup_id):
        raise ActivationError("invalid rollback backup identifier")
    backup = layout.backup_root / backup_id
    lock = lock_file(layout.lock)
    try:
        if layout.dropin.exists():
            raise ActivationError("runtime drain drop-in is present; resolve it first")
        assert_drained(layout, command)
        command(["systemctl", "stop", SERVICE])
        command(["systemctl", "stop", SOCKET])
        assert_no_helper_instances(command)
        restore_started = False
        try:
            helper_lock = lock_file(layout.helper_lock)
            try:
                assert_drained(layout, command)
                restore_started = True
                restore_files(layout, backup, allow_partial=False)
            finally:
                os.close(helper_lock)
        except Exception:
            if not restore_started:
                command(["systemctl", "start", SOCKET])
                command(["systemctl", "start", SERVICE])
            raise
        else:
            verify_base_unit(layout, command)
            command(["systemctl", "start", SOCKET])
            command(["systemctl", "start", SERVICE])
            if not service_active(SERVICE, command):
                raise ActivationError("legacy manager did not restart after rollback")
    finally:
        os.close(lock)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=Path("/root/ci-runner-four-pool-stage"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--activate", action="store_true")
    group.add_argument("--rollback", metavar="BACKUP_ID")
    parser.add_argument("--max-drain-seconds", type=int, default=3600)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("run as root using the named operator account's sudo")
    os.environ["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    if not 60 <= args.max_drain_seconds <= 7200:
        parser.error("--max-drain-seconds must be between 60 and 7200")
    try:
        if args.preflight:
            preflight(args.stage)
            print("DEV-50 four-pool source, installed policy, unit and host preflight passed")
        elif args.activate:
            backup = activate(args.stage, args.max_drain_seconds)
            print(f"DEV-50 four-pool binaries and policy activated; backup_id={backup.name}")
            print("No guest/JIT Docker check was run; validate trusted-heavy job and host soak")
        else:
            rollback(args.rollback)
            print(f"DEV-50 legacy runner policy restored from backup_id={args.rollback}")
        return 0
    except (ActivationError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"DEV-50 activation error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
