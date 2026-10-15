#!/usr/bin/env python3
"""Root-only, pinned helper recovery; no guest, credential, or policy replacement.

Copy the reviewed helper to a root-owned 0700 stage directory as
ci_runner_host_helper.py. Run --preflight, then --repair on Sanctuary. Optional
--release-gate-claims examines only three explicitly pinned queued Gate jobs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable


OLD_HELPER_SHA256 = "c8932841ee520853f2f6e127ad89fe94ca71e0b952f163a23a234a8f2baa0e35"
NEW_HELPER_SHA256 = "e36b48d938f108a348621664bd62fa0fe5fc55180d5ac3fce67070d72e59f34b"
LIVE_MANAGER_SHA256 = "c4490d84a0338e282bd6b98916293a2ba63f97d044d0cb6cbb31329cafde700f"
SERVICE = "ci-runner-manager.service"
SOCKET = "ci-runner-host-helper.socket"
MANAGER_USER = "ci-runner-manager"
GATE = "Tuinstra-DEV/gate"
GATE_RUN = 37735979706
GATE_HEAD = "9810d89000f14a32985102b14ff61952c865cdb1"
GATE_PR = 344
GATE_JOBS = (113175561905, 113175561934, 113175561978)
ACTIVE_JOB = 113175561862
QUARANTINE_UNTIL = (1 << 63) - 1
PROBE_CODE = r'''import importlib.util, json
from importlib.machinery import SourceFileLoader
from pathlib import Path
path = "/usr/local/bin/ci-runner-manager"
loader = SourceFileLoader("ci_runner_manager_recovery_probe", path)
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
cfg = module.load_config(Path("/etc/ci-runner/manager.toml"))
reply = module.helper(cfg, "resources")
resources = json.loads(reply.stdout)
if not isinstance(resources, list) or any(not isinstance(item, dict) for item in resources):
    raise RuntimeError("invalid helper resources")
print(json.dumps(resources, separators=(",", ":")))
'''


class RecoveryError(RuntimeError):
    pass


class StateCorrupt(RecoveryError):
    """The manager itself skips some malformed JSON; do not resume admission."""


@dataclass(frozen=True)
class Layout:
    manager: Path = Path("/usr/local/bin/ci-runner-manager")
    helper: Path = Path("/usr/local/libexec/ci-runner-host-helper")
    config: Path = Path("/etc/ci-runner/manager.toml")
    token: Path = Path("/etc/ci-runner/github.token")
    state: Path = Path("/var/lib/ci-runner-manager/state")
    runtime: Path = Path("/run/ci-runner-manager")
    manager_lock: Path = Path("/run/ci-runner-manager/manager.lock")
    helper_lock: Path = Path("/run/lock/ci-runner-host-helper.lock")
    operator_lock: Path = Path("/run/lock/ci-runner-loop-recovery.lock")
    backup_root: Path = Path("/var/backups/ci-runner-loop-recovery")
    overlay: Path = Path("/var/lib/ci-runner/overlay")
    qemu_logs: Path = Path("/var/log/libvirt/qemu")


HOST = Layout()
Command = Callable[..., subprocess.CompletedProcess[str]]


def run(argv: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, check=True, timeout=timeout)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def directory(path: Path, uid: int, mode: int) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode:
        raise RecoveryError(f"unsafe directory: {path}")


def regular(path: Path, uid: int, *, private: bool = False) -> tuple[bytes, os.stat_result]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_uid != uid or \
            (before.st_mode & (0o077 if private else 0o022)):
        raise RecoveryError(f"unsafe file: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
            raise RecoveryError(f"file changed while opening: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(), info
    finally:
        os.close(fd)


def checked_bytes(stage: Path, layout: Layout = HOST) -> tuple[bytes, bytes, os.stat_result]:
    directory(stage, 0, 0o700)
    candidate, _ = regular(stage / "ci_runner_host_helper.py", 0)
    old, metadata = regular(layout.helper, 0)
    manager, _ = regular(layout.manager, 0)
    if digest(candidate) != NEW_HELPER_SHA256 or digest(old) != OLD_HELPER_SHA256 or \
            digest(manager) != LIVE_MANAGER_SHA256:
        raise RecoveryError("pinned helper or live manager hash mismatch")
    compile(candidate, str(stage / "ci_runner_host_helper.py"), "exec")
    compile(manager, str(layout.manager), "exec")
    return candidate, old, metadata


def locked_file(path: Path, uid: int, gid: int) -> int:
    created = False
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created = True
    except FileExistsError:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or \
                (not created and info.st_uid != uid):
            raise RecoveryError(f"unsafe lock: {path}")
        if created:
            os.fchown(fd, uid, gid)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except Exception:
        os.close(fd)
        raise


def atomic_write(path: Path, data: bytes, uid: int, gid: int, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.recovery-", dir=path.parent)
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


def service_state(name: str, command: Command = run) -> tuple[str, int, int]:
    # Socket units expose ControlPID but do not expose a service MainPID.
    fields = {"ActiveState", "ControlPID"} if name == SOCKET else {"ActiveState", "MainPID", "ControlPID"}
    reply = command(["systemctl", "show", name, *["--property=" + key for key in sorted(fields)]])
    pairs = [line.split("=", 1) for line in reply.stdout.splitlines()]
    if any(len(pair) != 2 for pair in pairs) or len(pairs) != len(fields):
        raise RecoveryError(f"service state unknown: {name}")
    values = dict(pairs)
    if set(values) != fields or values["ActiveState"] not in {
            "active", "inactive", "failed", "activating", "deactivating", "reloading", "maintenance", "refreshing"}:
        raise RecoveryError(f"service state unknown: {name}")
    try:
        main_pid = 0 if name == SOCKET else int(values["MainPID"])
        control_pid = int(values["ControlPID"])
        if main_pid < 0 or control_pid < 0:
            raise ValueError("negative PID")
        return values["ActiveState"], main_pid, control_pid
    except ValueError as exc:
        raise RecoveryError(f"service state unknown: {name}") from exc


def no_active_helper_units(command: Command = run) -> None:
    reply = command(["systemctl", "list-units", "--all", "--plain", "--no-legend", "--no-pager",
                     "ci-runner-host-helper@*.service"])
    for line in reply.stdout.splitlines():
        words = line.split()
        if len(words) < 4 or not words[0].startswith("ci-runner-host-helper@") or \
                words[2:4] not in (["inactive", "dead"], ["failed", "failed"]):
            raise RecoveryError("helper instance is active or unknown")


def load_manager(layout: Layout = HOST):
    loader = SourceFileLoader("ci_runner_manager_recovery", str(layout.manager))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    old_bytecode_setting = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_bytecode_setting
    cfg = module.load_config(layout.config)
    if cfg["state_dir"] != str(layout.state) or cfg["helper_socket"] != "/run/ci-runner-host-helper.sock" or \
            cfg.get("runtime_dir") != str(layout.runtime) or cfg["lock_file"] != str(layout.manager_lock):
        raise RecoveryError("live manager paths differ from pinned recovery layout")
    return module, cfg


def runtime_lock(layout: Layout = HOST) -> int:
    identity = pwd.getpwnam(MANAGER_USER)
    if not layout.runtime.exists():
        layout.runtime.mkdir(mode=0o700)
        os.chown(layout.runtime, identity.pw_uid, identity.pw_gid)
    directory(layout.runtime, identity.pw_uid, 0o700)
    return locked_file(layout.manager_lock, identity.pw_uid, identity.pw_gid)


def backup_old(layout: Layout, old: bytes) -> Path:
    if not layout.backup_root.exists():
        layout.backup_root.mkdir(mode=0o700, parents=True)
    directory(layout.backup_root, 0, 0o700)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(4)
    backup = layout.backup_root / name
    backup.mkdir(mode=0o700)
    destination = backup / "old-helper.py"
    atomic_write(destination, old, 0, 0, 0o600)
    if digest(regular(destination, 0, private=True)[0]) != OLD_HELPER_SHA256:
        raise RecoveryError("helper backup verification failed")
    return backup


def load_staged_helper(stage: Path):
    path = stage / "ci_runner_host_helper.py"
    loader = SourceFileLoader("ci_runner_host_helper_recovery", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    old_bytecode_setting = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old_bytecode_setting
    return module


def first_job_evidence(stage: Path, layout: Layout, manager: Any, backup: Path) -> dict[str, Any]:
    """Read fixed first-job storage and sanitized shutdown reason before cleanup."""
    states, _history = raw_state(layout, manager)
    first = [state for state in states if state.get("repo") == GATE and
             state.get("trigger_job_id", state.get("job_id")) == ACTIVE_JOB]
    leases = {state.get("lease") for state in first}
    if len(leases) != 1 or not all(isinstance(value, str) for value in leases):
        raise RecoveryError("first Gate lease cannot be identified exactly")
    lease = next(iter(leases))
    helper = load_staged_helper(stage)
    if helper.OVERLAY_ROOT != layout.overlay or helper.HELPER_LOCK != layout.helper_lock:
        raise RecoveryError("staged helper storage paths differ")
    lease_dir, backing, work = helper.bounded_paths(lease)
    if lease_dir.parent != layout.overlay or backing.parent != lease_dir or work.parent != lease_dir:
        raise RecoveryError("first Gate lease paths differ")
    directory(layout.overlay, 0, 0o710)
    if not lease_dir.is_dir() or lease_dir.is_symlink() or not work.is_dir() or work.is_symlink():
        raise RecoveryError("first Gate work directory is unavailable")
    if backing.is_symlink() or not backing.is_file():
        raise RecoveryError("first Gate backing is unavailable")
    loop = helper.associated_loop(backing)  # New helper checks kernel dev+inode, not a path string.
    if loop is None:
        raise RecoveryError("first Gate backing has no verifiable loop identity")
    helper.verify_loop_binding(backing, loop, require_discard=False)
    if helper.host_mount_source(work) != loop or helper.local_mount_source(work) != loop:
        raise RecoveryError("first Gate work mount does not match verified loop")
    blocks = os.statvfs(work)
    free_bytes = blocks.f_bavail * blocks.f_frsize
    used_bytes = (blocks.f_blocks - blocks.f_bfree) * blocks.f_frsize
    if free_bytes < 0 or used_bytes < 0:
        raise RecoveryError("first Gate filesystem use is invalid")
    log_path = layout.qemu_logs / (helper.name(lease) + ".log")
    log_data, _ = regular(log_path, 0)
    if len(log_data) > 16 * 1024 * 1024:
        raise RecoveryError("first Gate QEMU log exceeds bounded inspection")
    reasons = re.findall(rb"shutting down, reason=([a-z_-]{1,32})(?:\r?\n|$)", log_data)
    if not reasons:
        raise RecoveryError("first Gate shutdown reason is unavailable")
    evidence = {"first_job_id": ACTIVE_JOB, "work_free_bytes": free_bytes,
                "work_used_bytes": used_bytes, "shutdown_reason": reasons[-1].decode("ascii")}
    atomic_write(backup / "first-job-metadata.json",
                 (json.dumps(evidence, sort_keys=True) + "\n").encode(), 0, 0, 0o600)
    return evidence


def actual_probe(layout: Layout = HOST, command: Command = run) -> list[dict[str, Any]]:
    if layout != HOST:
        raise RecoveryError("socket probe requires pinned live layout")
    reply = command(["runuser", "-u", MANAGER_USER, "--", "/usr/bin/python3", "-B", "-c", PROBE_CODE], timeout=20)
    try:
        resources = json.loads(reply.stdout)
    except json.JSONDecodeError as exc:
        raise RecoveryError("actual helper socket probe is unreadable") from exc
    if not isinstance(resources, list) or any(not isinstance(item, dict) for item in resources):
        raise RecoveryError("actual helper socket probe is invalid")
    return resources


def raw_state(layout: Layout, manager: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    owner = pwd.getpwnam(MANAGER_USER).pw_uid
    try:
        directory(layout.state, owner, 0o700)
    except (OSError, RecoveryError) as exc:
        raise StateCorrupt("runner state directory is unsafe") from exc
    states = []
    for entry in sorted(layout.state.iterdir()):
        if entry.name.startswith(("lease-", "cleanup-")) and entry.name.endswith(".json"):
            try:
                raw, _ = regular(entry, owner, private=True)
            except (OSError, RecoveryError) as exc:
                raise StateCorrupt("raw lease or cleanup state is unsafe") from exc
            try:
                value = json.loads(raw, object_pairs_hook=strict_object)
            except (json.JSONDecodeError, RecoveryError) as exc:
                raise StateCorrupt("raw lease or cleanup state is corrupt") from exc
            if not isinstance(value, dict):
                raise StateCorrupt("raw lease or cleanup state is invalid")
            states.append(value)
        elif entry.name.startswith(("lease-", "cleanup-")):
            raise StateCorrupt("raw lease or cleanup state has an unknown extension")
    history_file = layout.state / "dispatch-history.json"
    try:
        regular(history_file, owner, private=True)
        history = manager.DispatchHistory(layout.state).load()
    except Exception as exc:
        raise StateCorrupt("dispatch history is unreadable or invalid") from exc
    return states, history


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError("duplicate raw state field")
        result[key] = value
    return result


def gate_roster(client: Any) -> tuple[tuple[int, str, str, bool], ...]:
    payload = client.request("GET", "/repos/Tuinstra-DEV/gate/actions/runners?per_page=100&page=1")
    if not isinstance(payload, dict) or type(payload.get("total_count")) is not int or \
            not 0 <= payload["total_count"] <= 100 or not isinstance(payload.get("runners"), list) or \
            len(payload["runners"]) != payload["total_count"]:
        raise RecoveryError("GitHub runner roster is incomplete")
    result = []
    for runner in payload["runners"]:
        if not isinstance(runner, dict) or type(runner.get("id")) is not int or \
                not isinstance(runner.get("name"), str) or runner.get("status") not in {"online", "offline"} or \
                not isinstance(runner.get("busy"), bool):
            raise RecoveryError("GitHub runner roster is invalid")
        result.append((runner["id"], runner["name"], runner["status"], runner["busy"]))
    if len({item[0] for item in result}) != len(result):
        raise RecoveryError("GitHub runner roster has duplicate IDs")
    return tuple(sorted(result))


def gate_claims_safe(layout: Layout, manager: Any, resources: list[dict[str, Any]],
                     client: Any) -> bool:
    states, history = raw_state(layout, manager)
    keys = [f"{GATE}:{job}" for job in GATE_JOBS]
    if any(key not in history or history[key]["blocked_until"] != QUARANTINE_UNTIL for key in keys):
        return False
    if any(str(job) in json.dumps(state, sort_keys=True) for state in states for job in GATE_JOBS):
        return False
    lease_ids = {state.get("lease") for state in states if isinstance(state.get("lease"), str)}
    try:
        directory(layout.overlay, 0, 0o710)
        storage = list(layout.overlay.iterdir())
    except (OSError, RecoveryError):
        return False
    if any(item.is_symlink() or not item.is_dir() or item.name not in lease_ids
           for item in storage):
        return False  # Unknown storage may be a target's unrecorded lease.
    if any(not isinstance(item.get("lease"), str) or item["lease"] not in lease_ids for item in resources):
        return False
    roster_before = gate_roster(client)
    runner_ids = {state.get("runner_id") for state in states if state.get("repo") == GATE and
                  type(state.get("runner_id")) is int}
    if {item[0] for item in roster_before} != runner_ids or \
            any(status == "online" and not busy for _id, _name, status, busy in roster_before):
        return False  # Unknown registration, possibly a pending JIT for one of the claims.
    prefix = "/repos/Tuinstra-DEV/gate"
    pr = client.request("GET", prefix + f"/pulls/{GATE_PR}")
    run_data = client.request("GET", prefix + f"/actions/runs/{GATE_RUN}")
    if not isinstance(pr, dict) or pr.get("state") != "open" or \
            not isinstance(pr.get("head"), dict) or pr["head"].get("sha") != GATE_HEAD or \
            not isinstance(run_data, dict) or run_data.get("id") != GATE_RUN or \
            run_data.get("event") != "pull_request" or run_data.get("head_sha") != GATE_HEAD:
        return False
    first = []
    for job_id in GATE_JOBS:
        job = client.request("GET", prefix + f"/actions/jobs/{job_id}")
        if not isinstance(job, dict) or job.get("id") != job_id or job.get("run_id") != GATE_RUN or \
                job.get("status") != "queued" or job.get("runner_id") is not None or \
                job.get("runner_name") not in (None, "") or \
                not isinstance(job.get("labels"), list) or \
                set(job["labels"]) != {"self-hosted", "trusted-heavy"}:
            return False
        first.append(job)
    second = [client.request("GET", prefix + f"/actions/jobs/{job_id}") for job_id in GATE_JOBS]
    pr_second = client.request("GET", prefix + f"/pulls/{GATE_PR}")
    run_second = client.request("GET", prefix + f"/actions/runs/{GATE_RUN}")
    if first != second or pr != pr_second or run_data != run_second or \
            roster_before != gate_roster(client):
        return False
    return True


def release_gate_claims(layout: Layout, manager: Any, resources: list[dict[str, Any]],
                        backup_dir: Path) -> bool:
    try:
        token, _ = regular(layout.token, 0, private=True)
        if not token or b"\n" in token.strip():
            return False
        client = manager.GitHubClient(token.decode("utf-8").strip())
        safe = gate_claims_safe(layout, manager, resources, client)
    except StateCorrupt:
        raise
    except Exception:
        return False  # API, permission, or credential uncertainty retains every claim.
    if not safe:
        return False
    owner = pwd.getpwnam(MANAGER_USER)
    history_path = layout.state / "dispatch-history.json"
    original, info = regular(history_path, owner.pw_uid, private=True)
    atomic_write(backup_dir / "dispatch-history.json", original, 0, 0, 0o600)
    history = manager.DispatchHistory(layout.state)
    try:
        for job in GATE_JOBS:
            history.remove(f"{GATE}:{job}", int(time.time()))
        os.chown(history_path, info.st_uid, info.st_gid)
        os.chmod(history_path, stat.S_IMODE(info.st_mode))
        fd = os.open(history_path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        fd = os.open(layout.state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if any(f"{GATE}:{job}" in history.load() for job in GATE_JOBS):
            raise RecoveryError("Gate claim release did not persist")
    except Exception:
        atomic_write(history_path, original, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        raise
    return True


def repair(stage: Path, *, release_claims: bool = False, layout: Layout = HOST,
           command: Command = run, probe: Callable[..., list[dict[str, Any]]] = actual_probe,
           capture: Callable[..., dict[str, Any]] = first_job_evidence) -> tuple[Path, bool]:
    if os.geteuid() != 0:
        raise RecoveryError("root execution is required")
    candidate, old, metadata = checked_bytes(stage, layout)
    _initial_manager, initial_cfg = load_manager(layout)
    operator_fd = locked_file(layout.operator_lock, 0, 0)
    backup = None
    manager_fd = None
    helper_fd = None
    replaced = False
    try:
        fresh_candidate, fresh_old, fresh_metadata = checked_bytes(stage, layout)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)
        if fresh_candidate != candidate or fresh_old != old or \
                identity(fresh_metadata) != identity(metadata):
            raise RecoveryError("pinned helper changed before manager stop")
        manager, cfg = load_manager(layout)
        if cfg != initial_cfg:
            raise RecoveryError("manager configuration changed before manager stop")
        if service_state(SOCKET, command)[0] != "active":
            raise RecoveryError("helper socket is not active")
        command(["systemctl", "stop", SERVICE], timeout=30)
        state, main_pid, control_pid = service_state(SERVICE, command)
        if state not in ("inactive", "failed") or main_pid != 0 or control_pid != 0:
            raise RecoveryError("manager did not stop cleanly")
        manager_fd = runtime_lock(layout)
        no_active_helper_units(command)
        helper_fd = locked_file(layout.helper_lock, 0, 0)
        no_active_helper_units(command)
        if digest(regular(layout.helper, 0)[0]) != OLD_HELPER_SHA256:
            raise RecoveryError("installed helper changed before replacement")
        backup = backup_old(layout, old)
        capture(stage, layout, manager, backup)
        try:
            atomic_write(layout.helper, candidate, metadata.st_uid, metadata.st_gid,
                         stat.S_IMODE(metadata.st_mode))
        except Exception:
            # os.replace may have completed before a directory fsync failed.
            atomic_write(layout.helper, old, metadata.st_uid, metadata.st_gid,
                         stat.S_IMODE(metadata.st_mode))
            raise
        replaced = True
        os.close(helper_fd)
        helper_fd = None
        try:
            resources = probe(layout, command)
        except Exception as exc:
            # The helper socket remains enabled. Wait for the probe's service
            # instance to exit, then restore only the old helper binary.
            for _ in range(20):
                try:
                    no_active_helper_units(command)
                    break
                except RecoveryError:
                    time.sleep(0.25)
            else:
                raise RecoveryError("probe failed; helper unit remained active; manager remains stopped") from exc
            helper_fd = locked_file(layout.helper_lock, 0, 0)
            atomic_write(layout.helper, old, metadata.st_uid, metadata.st_gid,
                         stat.S_IMODE(metadata.st_mode))
            replaced = False
            raise RecoveryError("actual socket resources probe failed; old helper restored; manager remains stopped") from exc
        claims_released = False
        if release_claims:
            try:
                claims_released = release_gate_claims(layout, manager, resources, backup)
            except Exception as exc:
                raise RecoveryError("claim evidence is corrupt or unavailable; manager remains stopped") from exc
            if not claims_released:
                print("Gate claims retained: runner registration or assignment evidence is not conclusive", file=sys.stderr)
        if service_state(SOCKET, command)[0] != "active":
            raise RecoveryError("helper socket changed; manager remains stopped")
        os.close(manager_fd)
        manager_fd = None
        try:
            command(["systemctl", "start", SERVICE], timeout=30)
            state, main_pid, control_pid = service_state(SERVICE, command)
            if state != "active" or main_pid <= 0 or control_pid != 0:
                raise RecoveryError("manager restart status is not a running service")
        except Exception as start_error:
            try:
                command(["systemctl", "stop", SERVICE], timeout=30)
                state, main_pid, control_pid = service_state(SERVICE, command)
                if state not in ("inactive", "failed") or main_pid != 0 or control_pid != 0:
                    raise RecoveryError("manager stop state is not proven")
            except Exception as restop_error:
                raise RecoveryError("indeterminate manager state after failed restart") from restop_error
            raise RecoveryError("manager restart failed; verified stopped") from start_error
        return backup, claims_released
    finally:
        if helper_fd is not None:
            os.close(helper_fd)
        if manager_fd is not None:
            os.close(manager_fd)
        os.close(operator_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, type=Path,
                        help="root-owned 0700 directory containing ci_runner_host_helper.py")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--repair", action="store_true")
    parser.add_argument("--release-gate-claims", action="store_true")
    args = parser.parse_args(argv)
    if args.release_gate_claims and not args.repair:
        parser.error("claim release requires --repair")
    try:
        if os.geteuid() != 0:
            raise RecoveryError("root execution is required")
        checked_bytes(args.stage)
        load_manager()
        if args.preflight:
            print("Pinned helper recovery preflight passed; no host state changed")
        else:
            backup, released = repair(args.stage, release_claims=args.release_gate_claims)
            print(f"Helper recovered; backup={backup}; Gate claims released={str(released).lower()}")
        return 0
    except (RecoveryError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"Recovery stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
