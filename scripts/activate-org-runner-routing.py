#!/usr/bin/env python3
"""Activate reviewed DEV-50 org routing after a natural runner drain.

Stage this file and ci_runner_manager.py in a root-owned directory. The org
credential and GitHub runner group must already have been provisioned. This
tool changes only the manager binary, its TOML, and one systemd drop-in.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from typing import Callable
import urllib.error
import urllib.request


OLD_MANAGER_SHA = "37061633484a206a027c0079bc47cfb904d8189a97e3bd3a06ff4bfb59be2249"
NEW_MANAGER_SHA = "1d141705f975efafcdc85c8941f3879c395b4988bc1373dd4750b995935968e7"
HELPER_SHA = "fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613"
UNIT_SHA = "c99fdf23b9c50971be678565db54e85d5227040ff1108ccb3622a9c2ae4d806d"
ORG_REF_PREFIX = "Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@"
ORG_CREDENTIAL = "/run/credentials/ci-runner-manager.service/org_github_token"
ORG_TOKEN_SOURCE = "/etc/ci-runner/org-github.token"
UNIT_OBJECT = "/org/freedesktop/systemd1/unit/ci_2drunner_2dmanager_2eservice"
SERVICE = "ci-runner-manager.service"
SOCKET = "ci-runner-host-helper.socket"
DROPIN_BYTES = b"[Service]\nLoadCredential=org_github_token:/etc/ci-runner/org-github.token\n"
DRAIN_BYTES = ("[Service]\nType=oneshot\nExecStart=\n"
               "ExecStart=/usr/local/bin/ci-runner-manager --config "
               "/etc/ci-runner/manager.toml reconcile\nRestart=no\n").encode()
REPOSITORIES = {
    "Tuinstra-DEV/console": 1154259119, "Tuinstra-DEV/gate": 1273386924,
    "Tuinstra-DEV/marcel-site": 960069181, "Tuinstra-DEV/notify": 1304692512,
    "Tuinstra-DEV/openairco": 1355089923, "Tuinstra-DEV/openairco-site": 1331331146,
    "Tuinstra-DEV/status": 1378644550, "Tuinstra-DEV/tracker": 1301969191,
    "Tuinstra-DEV/tuinstra-site": 960069678, "Tuinstra-DEV/wodiq-app": 1238121712,
    "Tuinstra-DEV/wodiq-platform": 1379333293, "Tuinstra-DEV/wodiq-site": 1264767351,
}


class ActivationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Layout:
    manager: Path = Path("/usr/local/bin/ci-runner-manager")
    helper: Path = Path("/usr/local/libexec/ci-runner-host-helper")
    config: Path = Path("/etc/ci-runner/manager.toml")
    org_token: Path = Path(ORG_TOKEN_SOURCE)
    unit: Path = Path("/etc/systemd/system/ci-runner-manager.service")
    dropin: Path = Path("/etc/systemd/system/ci-runner-manager.service.d/20-org-credential.conf")
    drain: Path = Path("/run/systemd/system/ci-runner-manager.service.d/dev50-org-drain.conf")
    state: Path = Path("/var/lib/ci-runner-manager/state")
    overlay: Path = Path("/var/lib/ci-runner/overlay")
    backup_root: Path = Path("/var/backups/ci-runner-org-routing")
    lock: Path = Path("/run/lock/ci-runner-org-activation.lock")
    helper_lock: Path = Path("/run/lock/ci-runner-host-helper.lock")


HOST = Layout()
Command = Callable[..., subprocess.CompletedProcess[str]]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_dir(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ActivationError(f"unsafe root-owned directory: {path}")


def read_file(path: Path, *, mode: int | None = None) -> tuple[bytes, os.stat_result]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or \
            (mode is not None and stat.S_IMODE(info.st_mode) != mode):
        raise ActivationError(f"unsafe root-owned file: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode) != \
                (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_gid, opened.st_mode):
            raise ActivationError(f"file changed during read: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read(), opened
    finally:
        os.close(fd)


def run(args: list[str], *, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActivationError(f"{Path(args[0]).name} unavailable or timed out") from exc
    if check and result.returncode:
        raise ActivationError(f"{Path(args[0]).name} failed with exit {result.returncode}")
    return result


def active(name: str, command: Command = run) -> bool:
    return command(["systemctl", "is-active", "--quiet", name], check=False).returncode == 0


def lock(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        os.close(fd)
        raise ActivationError("activation lock is unsafe")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise ActivationError("activation or host helper is busy") from exc
    return fd


def atomic_write(path: Path, data: bytes, info: os.stat_result) -> None:
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.dev50-", dir=path.parent)
    try:
        os.fchmod(fd, stat.S_IMODE(info.st_mode))
        os.fchown(fd, info.st_uid, info.st_gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def create_file(path: Path, data: bytes, mode: int = 0o644) -> tuple[int, int]:
    checked_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    info = os.fstat(fd)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return info.st_dev, info.st_ino
    except Exception:
        if path.exists() and path.lstat().st_ino == info.st_ino:
            path.unlink()
        raise


def remove_own_file(path: Path, identity: tuple[int, int], content: bytes) -> None:
    current, info = read_file(path)
    if (info.st_dev, info.st_ino) != identity or current != content:
        raise ActivationError("installer-owned drop-in changed")
    path.unlink()


def credential_pairs(command: Command = run) -> set[tuple[str, str]]:
    response = command(["busctl", "--json=short", "get-property", "org.freedesktop.systemd1",
                        UNIT_OBJECT, "org.freedesktop.systemd1.Service", "LoadCredential"])
    try:
        document = json.loads(response.stdout)
        pairs = document["data"]
        if document["type"] != "a(ss)" or not isinstance(pairs, list) or \
                any(not isinstance(p, list) or len(p) != 2 or
                    not all(isinstance(value, str) for value in p) for p in pairs) or \
                len(pairs) != len(set(map(tuple, pairs))):
            raise ValueError
        return set(map(tuple, pairs))
    except (KeyError, TypeError, ValueError) as exc:
        raise ActivationError("effective credential metadata is invalid") from exc


BASE_CREDENTIALS = {("github_token", "/etc/ci-runner/github.token")}
ORG_CREDENTIALS = BASE_CREDENTIALS | {("org_github_token", ORG_TOKEN_SOURCE)}


def verify_unit(layout: Layout, command: Command, *, org: bool) -> None:
    unit, _ = read_file(layout.unit)
    if sha(unit) != UNIT_SHA:
        raise ActivationError("manager unit differs from reviewed base")
    expected = {"FragmentPath": str(layout.unit), "Type": "simple", "Restart": "always",
                "User": "ci-runner-manager", "Group": "ci-runner-manager"}
    for key, value in expected.items():
        actual = command(["systemctl", "show", SERVICE, f"--property={key}", "--value"]).stdout.strip()
        if actual != value:
            raise ActivationError(f"manager unit differs at {key}")
    start = command(["systemctl", "show", SERVICE, "--property=ExecStart", "--value"]).stdout
    if start.count("argv[]=") != 1 or \
            "argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30" not in start:
        raise ActivationError("manager command differs from reviewed daemon")
    dropins = command(["systemctl", "show", SERVICE, "--property=DropInPaths", "--value"]).stdout.strip()
    if dropins != (str(layout.dropin) if org else ""):
        raise ActivationError("manager unit has unreviewed drop-ins")
    if credential_pairs(command) != (ORG_CREDENTIALS if org else BASE_CREDENTIALS):
        raise ActivationError("effective manager credential binding differs")


def candidate_config(old_bytes: bytes, workflow_sha: str, group_id: int) -> tuple[dict, bytes]:
    if not isinstance(workflow_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", workflow_sha) or \
            type(group_id) is not int or group_id <= 1:
        raise ActivationError("reviewed workflow SHA or group ID is invalid")
    try:
        text = old_bytes.decode("utf-8")
        old = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ActivationError("installed manager configuration is invalid") from exc
    exact = {"pool_mode": "four", "max_concurrency": 4, "max_heavy": 2, "max_medium": 2,
             "runner_vcpus": 4, "runner_memory_mib": 6144,
             "medium_runner_vcpus": 2, "medium_runner_memory_mib": 3072,
             "heavy_disk_reservation_gib": 24, "medium_disk_reservation_gib": 4,
             "min_free_disk_gib": 60, "host_memory_reserve_mib": 4096,
             "max_lease_seconds": 7200, "runner_group_id": 1,
             "storage_mode": "bounded-loop", "runner_label": "trusted-heavy",
             "medium_runner_label": "trusted-medium", "allowed_owner": "Tuinstra-DEV",
             "github_token_file": "/run/credentials/ci-runner-manager.service/github_token",
             "github_api_url": "https://api.github.com", "overlay_root": "/var/lib/ci-runner/overlay",
             "state_dir": "/var/lib/ci-runner-manager/state",
             "helper_socket": "/run/ci-runner-host-helper.sock"}
    if any(type(old.get(key)) is not type(value) or old.get(key) != value
           for key, value in exact.items()) or \
            any(key.startswith("org_") for key in old) or \
            any(re.match(r"^\s*\[", line) for line in text.splitlines()):
        raise ActivationError("installed policy is not reviewed four-pool 24-GiB configuration")
    legacy_repositories = ["Tuinstra-DEV/gate", "Tuinstra-DEV/wodiq-app",
                           "Tuinstra-DEV/wodiq-platform", "Tuinstra-DEV/tracker",
                           "Tuinstra-DEV/notify", "Tuinstra-DEV/console",
                           "Tuinstra-DEV/wodiq-site", "Tuinstra-DEV/marcel-site",
                           "Tuinstra-DEV/tuinstra-site"]
    if old.get("repositories") != legacy_repositories:
        raise ActivationError("installed legacy repository allowlist differs")
    additions = {"org_routing_enabled": True, "org_github_token_file": ORG_CREDENTIAL,
                 "org_runner_group_id": group_id,
                 "org_workflow_ref": ORG_REF_PREFIX + workflow_sha,
                 "org_repository_ids": REPOSITORIES}
    lines = ["org_routing_enabled = true",
             f"org_github_token_file = {json.dumps(ORG_CREDENTIAL)}",
             f"org_runner_group_id = {group_id}",
             f"org_workflow_ref = {json.dumps(additions['org_workflow_ref'])}",
             "[org_repository_ids]"]
    lines += [f"{json.dumps(repo)} = {identifier}"
              for repo, identifier in sorted(REPOSITORIES.items())]
    updated = (text.rstrip("\n") + "\n" + "\n".join(lines) + "\n").encode()
    if tomllib.loads(updated.decode()) != {**old, **additions}:
        raise ActivationError("org configuration transformation is not isolated")
    return {**old, **additions}, updated


def verify_group(cfg: dict, token: bytes) -> None:
    # This read-only GET happens before drain or any installed-file mutation.
    try:
        authorization = token.decode("utf-8").strip()
        if not authorization or "\n" in authorization or "\r" in authorization:
            raise ValueError
        base = f"https://api.github.com/orgs/Tuinstra-DEV/actions/runner-groups/{cfg['org_runner_group_id']}"
        def get(url: str) -> dict:
            request = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {authorization}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }, method="GET")
            opener = urllib.request.build_opener(urllib.request.HTTPHandler(),
                                                 urllib.request.HTTPSHandler(),
                                                 urllib.request.ProxyHandler({}),
                                                 NoRedirect())
            with opener.open(request, timeout=20) as response:
                if response.status != 200:
                    raise ValueError
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise ValueError
            return json.loads(body)
        group = get(base)
        if not isinstance(group, dict) or group.get("id") != cfg["org_runner_group_id"] or \
                group.get("name") != "sanctuary-trusted-verification" or \
                group.get("visibility") != "selected" or group.get("default") is not False or \
                group.get("inherited") is not False or \
                group.get("allows_public_repositories") is not False or \
                group.get("restricted_to_workflows") is not True or \
                group.get("selected_workflows") != [cfg["org_workflow_ref"]]:
            raise ValueError
        selection = get(base + "/repositories?per_page=100&page=1")
        if not isinstance(selection, dict) or selection.get("total_count") != len(REPOSITORIES) or \
                not isinstance(selection.get("repositories"), list) or \
                len(selection["repositories"]) != len(REPOSITORIES):
            raise ValueError
        actual = {}
        for item in selection["repositories"]:
            if not isinstance(item, dict) or item.get("private") is not True or \
                    not isinstance(item.get("full_name"), str) or \
                    type(item.get("id")) is not int or item["full_name"] in actual:
                raise ValueError
            actual[item["full_name"]] = item["id"]
        if actual != REPOSITORIES:
            raise ValueError
    except (OSError, UnicodeError, ValueError, KeyError, TypeError,
            urllib.error.URLError) as exc:
        raise ActivationError("organization runner group preflight failed") from exc


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def preflight(stage: Path, sha40: str, group_id: int, layout: Layout = HOST,
              command: Command = run, group_probe: Callable[[dict, bytes], None] = verify_group):
    for path in (stage, layout.config.parent, layout.manager.parent, layout.helper.parent,
                 layout.unit.parent, layout.org_token.parent):
        checked_dir(path)
    if layout.dropin.exists() or layout.drain.exists():
        raise ActivationError("org credential or runtime drain drop-in already exists")
    source, source_info = read_file(stage / "ci_runner_manager.py")
    if stat.S_IMODE(source_info.st_mode) not in {0o644, 0o755} or source_info.st_gid != 0:
        raise ActivationError("staged manager source permissions differ")
    manager, manager_info = read_file(layout.manager, mode=0o755)
    helper, helper_info = read_file(layout.helper, mode=0o755)
    unit, unit_info = read_file(layout.unit, mode=0o644)
    config, config_info = read_file(layout.config, mode=0o640)
    token, token_info = read_file(layout.org_token, mode=0o600)
    if manager_info.st_gid != 0 or helper_info.st_gid != 0 or unit_info.st_gid != 0 or \
            token_info.st_gid != 0 or \
            config_info.st_gid != grp.getgrnam("ci-runner-manager").gr_gid:
        raise ActivationError("installed runner owner or group differs")
    if (sha(source), sha(manager), sha(helper), sha(unit)) != \
            (NEW_MANAGER_SHA, OLD_MANAGER_SHA, HELPER_SHA, UNIT_SHA):
        raise ActivationError("staged or installed runner SHA-256 differs")
    cfg, new_config = candidate_config(config, sha40, group_id)
    verify_unit(layout, command, org=False)
    group_probe(cfg, token)
    return {"manager": manager, "config": config}, \
        {"manager": source, "config": new_config}, \
        {"manager": manager_info, "config": config_info}


def assert_drained(layout: Layout, command: Command) -> None:
    domains = command(["virsh", "--connect", "qemu:///system", "list", "--all", "--name"]).stdout.splitlines()
    if any(name.startswith("sanctuary-ci-") for name in domains):
        raise ActivationError("runner VM remains during drain")
    for path in (layout.state, layout.overlay):
        entries = list(path.iterdir())
        if path == layout.overlay and entries or \
                path == layout.state and any(item.name.startswith(("lease-", "cleanup-")) or
                                             item.name.endswith(".tmp") for item in entries):
            raise ActivationError("runner lease or cleanup obligation remains")


def no_helpers(command: Command) -> None:
    result = command(["systemctl", "list-units", "--all", "--type=service",
                      "--state=activating,running,reloading,deactivating", "--plain", "--no-legend",
                      "ci-runner-host-helper@*.service"])
    if result.stdout.strip():
        raise ActivationError("runner host helper remains active")


def drain(layout: Layout, seconds: int, command: Command, pause=time.sleep) -> tuple[int, int]:
    if not active(SERVICE, command):
        raise ActivationError("existing manager is not active")
    command(["systemctl", "stop", SERVICE])
    checked_dir(layout.drain.parent.parent)
    layout.drain.parent.mkdir(mode=0o755, exist_ok=True)
    identity = create_file(layout.drain, DRAIN_BYTES)
    try:
        command(["systemctl", "daemon-reload"])
        kind = command(["systemctl", "show", SERVICE, "--property=Type", "--value"]).stdout.strip()
        restart = command(["systemctl", "show", SERVICE, "--property=Restart", "--value"]).stdout.strip()
        start = command(["systemctl", "show", SERVICE, "--property=ExecStart", "--value"]).stdout
        if kind != "oneshot" or restart != "no" or start.count("argv[]=") != 1 or \
                "argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml reconcile" not in start or \
                credential_pairs(command) != BASE_CREDENTIALS:
            raise ActivationError("runtime reconcile-only manager is not verified")
        deadline = time.monotonic() + seconds
        while True:
            command(["systemctl", "start", SERVICE], timeout=180)
            try:
                assert_drained(layout, command)
                return identity
            except ActivationError:
                if time.monotonic() >= deadline:
                    raise ActivationError("runner drain timed out")
                pause(min(15.0, max(0.0, deadline - time.monotonic())))
    except Exception:
        remove_own_file(layout.drain, identity, DRAIN_BYTES)
        command(["systemctl", "daemon-reload"])
        raise


def backup(layout: Layout, original: dict[str, bytes], replacement: dict[str, bytes],
           infos: dict[str, os.stat_result]) -> Path:
    checked_dir(layout.backup_root.parent)
    layout.backup_root.mkdir(mode=0o700, exist_ok=True)
    checked_dir(layout.backup_root)
    folder = layout.backup_root / (datetime.now(timezone.utc).strftime("dev50-%Y%m%dT%H%M%SZ-") +
                                   secrets.token_hex(4))
    folder.mkdir(mode=0o700)
    root_fd = os.open(layout.backup_root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(root_fd)
    finally:
        os.close(root_fd)
    records = {}
    for key in ("manager", "config"):
        data = original[key]
        record = infos[key]
        fd = os.open(folder / key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        records[key] = {"old": sha(data), "new": sha(replacement[key]),
                        "uid": record.st_uid, "gid": record.st_gid,
                        "mode": stat.S_IMODE(record.st_mode)}
    fd = os.open(folder / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(json.dumps(records, sort_keys=True).encode())
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return folder


def record_dropin(folder: Path, identity: tuple[int, int]) -> None:
    data, info = read_file(folder / "manifest.json", mode=0o600)
    manifest = json.loads(data)
    if "dropin" in manifest:
        raise ActivationError("backup drop-in identity already recorded")
    manifest["dropin"] = {"dev": identity[0], "ino": identity[1],
                            "sha256": sha(DROPIN_BYTES)}
    atomic_write(folder / "manifest.json", json.dumps(manifest, sort_keys=True).encode(), info)


def recorded_dropin(folder: Path) -> tuple[int, int]:
    checked_dir(folder)
    try:
        record = json.loads(read_file(folder / "manifest.json", mode=0o600)[0])["dropin"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ActivationError("backup drop-in identity is invalid") from exc
    if not isinstance(record, dict) or type(record.get("dev")) is not int or \
            type(record.get("ino")) is not int or record.get("sha256") != sha(DROPIN_BYTES):
        raise ActivationError("backup drop-in identity is invalid")
    return record["dev"], record["ino"]


def restore(layout: Layout, folder: Path, *, partial: bool) -> None:
    if folder.parent != layout.backup_root or not re.fullmatch(
            r"dev50-\d{8}T\d{6}Z-[a-f0-9]{8}", folder.name):
        raise ActivationError("backup identifier is invalid")
    checked_dir(folder)
    try:
        records = json.loads(read_file(folder / "manifest.json", mode=0o600)[0])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise ActivationError("backup manifest is invalid") from exc
    paths = {"manager": layout.manager, "config": layout.config}
    recovered = {}
    for key, path in paths.items():
        recovered[key] = read_file(folder / key, mode=0o600)[0]
        current = read_file(path)[0]
        record = records.get(key)
        if not isinstance(record, dict) or sha(recovered[key]) != record.get("old") or \
                sha(current) not in ({record.get("old"), record.get("new")} if partial
                                     else {record.get("new")}):
            raise ActivationError("backup or installed file changed")
    for key, path in paths.items():
        record = records[key]
        _, current_info = read_file(path)
        # The metadata is recorded but only the reviewed original modes are allowed.
        if record["uid"] != 0 or record["mode"] not in ({0o755} if key == "manager"
                                                       else {0o640}) or \
                (current_info.st_uid, current_info.st_gid,
                 stat.S_IMODE(current_info.st_mode)) != \
                (record["uid"], record["gid"], record["mode"]):
            raise ActivationError("backup metadata differs from reviewed installation")
        atomic_write(path, recovered[key], current_info)


def activate(stage: Path, sha40: str, group_id: int, seconds: int,
             layout: Layout = HOST, command: Command = run,
             group_probe: Callable[[dict, bytes], None] = verify_group) -> Path:
    activation_lock = lock(layout.lock)
    drain_identity = None
    dropin_identity = None
    folder = None
    try:
        original, replacement, infos = preflight(stage, sha40, group_id, layout, command, group_probe)
    except Exception:
        os.close(activation_lock)
        raise
    try:
        drain_identity = drain(layout, seconds, command)
        command(["systemctl", "stop", SERVICE])
        command(["systemctl", "stop", SOCKET])
        if active(SERVICE, command) or active(SOCKET, command):
            raise ActivationError("admission services did not stop")
        no_helpers(command)
        helper_lock = lock(layout.helper_lock)
        try:
            assert_drained(layout, command)
            if read_file(layout.manager)[0] != original["manager"] or \
                    read_file(layout.config)[0] != original["config"]:
                raise ActivationError("installed policy changed during drain")
            folder = backup(layout, original, replacement, infos)
            for key, path in (("manager", layout.manager), ("config", layout.config)):
                atomic_write(path, replacement[key], infos[key])
                if sha(read_file(path)[0]) != sha(replacement[key]):
                    raise ActivationError("installed file readback failed")
            layout.dropin.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            dropin_identity = create_file(layout.dropin, DROPIN_BYTES)
            record_dropin(folder, dropin_identity)
        finally:
            os.close(helper_lock)
        remove_own_file(layout.drain, drain_identity, DRAIN_BYTES)
        drain_identity = None
        command(["systemctl", "daemon-reload"])
        verify_unit(layout, command, org=True)
        assert_drained(layout, command)
        command(["systemctl", "start", SOCKET])
        command(["systemctl", "start", SERVICE])
        if not active(SOCKET, command) or not active(SERVICE, command):
            raise ActivationError("org-routed manager did not become active")
        return folder
    except Exception as error:
        reason = str(error) if isinstance(error, ActivationError) else type(error).__name__
        try:
            command(["systemctl", "stop", SERVICE], check=False)
            command(["systemctl", "stop", SOCKET], check=False)
            if active(SERVICE, command) or active(SOCKET, command):
                raise ActivationError("admission services could not be stopped")
            if folder is not None:
                no_helpers(command)
                assert_drained(layout, command)
            elif layout.drain.exists() and drain_identity is None:
                raise ActivationError("runtime drain drop-in state is ambiguous")
            elif read_file(layout.manager)[0] != original["manager"] or \
                    read_file(layout.config)[0] != original["config"]:
                raise ActivationError("installed policy changed during failed drain")
            if folder is not None:
                restore(layout, folder, partial=True)
            if dropin_identity is not None:
                remove_own_file(layout.dropin, dropin_identity, DROPIN_BYTES)
            if drain_identity is not None:
                remove_own_file(layout.drain, drain_identity, DRAIN_BYTES)
            command(["systemctl", "daemon-reload"])
            verify_unit(layout, command, org=False)
            command(["systemctl", "start", SOCKET])
            command(["systemctl", "start", SERVICE])
            if not active(SERVICE, command):
                raise ActivationError("original manager did not restart")
        except Exception as recovery:
            raise ActivationError("activation failed; admission remains stopped; "
                                  f"backup={folder.name if folder else 'none'}; "
                                  f"failure={reason}; recovery={type(recovery).__name__}") from error
        raise ActivationError(f"activation aborted; original manager restored; failure={reason}") from error
    finally:
        os.close(activation_lock)


def rollback(backup_id: str, layout: Layout = HOST, command: Command = run) -> None:
    folder = layout.backup_root / backup_id
    if not re.fullmatch(r"dev50-\d{8}T\d{6}Z-[a-f0-9]{8}", backup_id):
        raise ActivationError("invalid backup ID")
    activation_lock = lock(layout.lock)
    try:
        if layout.drain.exists():
            raise ActivationError("runtime drain is present")
        credential, dropin_info = read_file(layout.dropin, mode=0o644)
        if credential != DROPIN_BYTES or \
                (dropin_info.st_dev, dropin_info.st_ino) != recorded_dropin(folder):
            raise ActivationError("org credential drop-in changed")
        # Rollback is deliberately performed only after all existing org jobs finish.
        assert_drained(layout, command)
        command(["systemctl", "stop", SERVICE])
        command(["systemctl", "stop", SOCKET])
        no_helpers(command)
        helper_lock = lock(layout.helper_lock)
        try:
            assert_drained(layout, command)
            restore(layout, folder, partial=False)
            remove_own_file(layout.dropin, (dropin_info.st_dev, dropin_info.st_ino), DROPIN_BYTES)
        finally:
            os.close(helper_lock)
        command(["systemctl", "daemon-reload"])
        verify_unit(layout, command, org=False)
        command(["systemctl", "start", SOCKET])
        command(["systemctl", "start", SERVICE])
        if not active(SERVICE, command):
            raise ActivationError("original manager did not restart")
    finally:
        os.close(activation_lock)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=Path("/root/ci-runner-org-routing-stage"))
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--preflight", action="store_true")
    actions.add_argument("--activate", action="store_true")
    actions.add_argument("--rollback", metavar="BACKUP_ID")
    parser.add_argument("--workflow-sha")
    parser.add_argument("--group-id", type=int)
    parser.add_argument("--max-drain-seconds", type=int, default=3600)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("run as root via the named operator account's sudo")
    os.environ["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    try:
        checked_dir(Path(__file__).parent)
        read_file(Path(__file__))
    except (ActivationError, OSError) as exc:
        parser.error(f"operator script is not in a root-owned stage: {type(exc).__name__}")
    if not 60 <= args.max_drain_seconds <= 7200:
        parser.error("drain window must be 60–7200 seconds")
    if not args.rollback and (args.workflow_sha is None or args.group_id is None):
        parser.error("--workflow-sha and --group-id are required")
    try:
        if args.preflight:
            preflight(args.stage, args.workflow_sha, args.group_id)
            print("DEV-50 org group, staged manager, and installed runner policy preflight passed")
        elif args.activate:
            folder = activate(args.stage, args.workflow_sha, args.group_id, args.max_drain_seconds)
            print(f"DEV-50 org routing activated; backup_id={folder.name}")
        else:
            rollback(args.rollback)
            print(f"DEV-50 original four-pool manager restored; backup_id={args.rollback}")
        return 0
    except (ActivationError, OSError, ValueError, KeyError) as exc:
        print(f"DEV-50 org activation error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
