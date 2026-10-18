#!/usr/bin/env python3
"""Bounded DEV-50 manager upgrade under the reviewed three-ref org policy.

Stage this file, activate-org-runner-routing.py, and ci_runner_manager.py in one
root-owned directory. This revision changes only the installed manager binary;
the config and GitHub runner-group selection are checked but unchanged.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import tomllib
from typing import Callable
import urllib.error
import urllib.request


OLD_MANAGER_SHA = "2ace71bf8872ce2090ff5e15668f08f906e84519f920240eafbe23d9590017b4"
NEW_MANAGER_SHA = "232d37364c0754bd59aefbf44fee9ba5704def038bbda94514975f857dd61e9f"
ACTIVATION_SHA = "663b3c3979b16230a17b77c6af0a2a6dc1ac5b4f84f6f277ed211312ab4e64b5"
KNOWN_OLD_SHAS = frozenset({"36f32aebe315274df8825674b7bd0ced637ab056",
                            "e1a9f09504b903120e9b5297eff64f58a891c907"})
ADMITTED_WORKFLOW_SHA = "23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e"
SERVICE = "ci-runner-manager.service"
SOCKET = "ci-runner-host-helper.socket"
ORG = "Tuinstra-DEV"
GROUP_NAME = "sanctuary-trusted-verification"
REF_PREFIX = ORG + "/devops/.github/workflows/reusable-trusted-verification.yml@"
BACKUP_PATTERN = r"dev50-\d{8}T\d{6}Z-[a-f0-9]{8}"

_source = Path(__file__).with_name("activate-org-runner-routing.py")


def load_reviewed_base():
    """Execute only the bytes checked through a no-follow, root-owned file FD.

    The matching unprivileged owner case permits local offline tests; the CLI
    refuses non-root execution, so it cannot weaken production loading.
    """
    fd = os.open(_source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        path_info = _source.lstat()
        expected_owner = os.geteuid()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != expected_owner or \
                info.st_gid not in ({0} if expected_owner == 0 else {os.getegid()}) or \
                info.st_mode & 0o022 or \
                (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) != \
                (path_info.st_dev, path_info.st_ino, path_info.st_mode,
                 path_info.st_uid, path_info.st_gid):
            raise RuntimeError("reviewed activation module metadata differs")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read()
        import hashlib
        if hashlib.sha256(data).hexdigest() != ACTIVATION_SHA:
            raise RuntimeError("reviewed activation module hash differs")
    finally:
        os.close(fd)
    if os.geteuid() == 0:
        parent = _source.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
            raise RuntimeError("reviewed activation module stage is unsafe")
    spec = importlib.util.spec_from_loader("dev50_activation_primitives", loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(data, str(_source), "exec"), module.__dict__)
    return module


base = load_reviewed_base()

Error = base.ActivationError
Command = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Layout(base.Layout):
    backup_root: Path = Path("/var/backups/ci-runner-workflow-transition")
    lock: Path = Path("/run/lock/ci-runner-org-activation.lock")
    drain: Path = Path("/run/systemd/system/ci-runner-manager.service.d/dev50-workflow-drain.conf")


HOST = Layout()


def refs(cfg: dict) -> tuple[str, ...]:
    single, many = "org_workflow_ref" in cfg, "org_workflow_refs" in cfg
    if single == many:
        raise Error("organization workflow policy has ambiguous reference fields")
    value = [cfg["org_workflow_ref"]] if single else cfg["org_workflow_refs"]
    if not isinstance(value, list) or not 1 <= len(value) <= 3 or \
            any(not isinstance(ref, str) or not ref.startswith(REF_PREFIX) or
                re.fullmatch(r"[a-f0-9]{40}", ref[len(REF_PREFIX):]) is None
                for ref in value) or len(set(value)) != len(value):
        raise Error("organization workflow references are not distinct exact commits")
    return tuple(value)


def parse_config(data: bytes) -> dict:
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise Error("installed manager configuration is invalid") from exc


def candidate_config(data: bytes, new_sha: str, group_id: int) -> tuple[bytes, tuple[str, ...], tuple[str, ...], bool]:
    if new_sha != ADMITTED_WORKFLOW_SHA or type(group_id) is not int or group_id <= 1:
        raise Error("reviewed workflow SHA or group ID is invalid")
    old = parse_config(data)
    exact = {"org_routing_enabled": True, "org_github_token_file": base.ORG_CREDENTIAL,
             "org_runner_group_id": group_id, "org_repository_ids": base.REPOSITORIES,
             "pool_mode": "four", "max_concurrency": 4, "max_heavy": 2,
             "max_medium": 2, "runner_vcpus": 4, "runner_memory_mib": 6144,
             "medium_runner_vcpus": 2, "medium_runner_memory_mib": 3072,
             "heavy_disk_reservation_gib": 24, "medium_disk_reservation_gib": 4,
             "min_free_disk_gib": 60, "host_memory_reserve_mib": 4096,
             "max_lease_seconds": 7200, "runner_group_id": 1,
             "storage_mode": "bounded-loop", "runner_label": "trusted-heavy",
             "medium_runner_label": "trusted-medium", "allowed_owner": ORG,
             "github_token_file": "/run/credentials/ci-runner-manager.service/github_token",
             "github_api_url": "https://api.github.com", "overlay_root": "/var/lib/ci-runner/overlay",
             "state_dir": "/var/lib/ci-runner-manager/state",
             "helper_socket": "/run/ci-runner-host-helper.sock"}
    exact["repositories"] = ["Tuinstra-DEV/gate", "Tuinstra-DEV/wodiq-app",
        "Tuinstra-DEV/wodiq-platform", "Tuinstra-DEV/tracker", "Tuinstra-DEV/notify",
        "Tuinstra-DEV/console", "Tuinstra-DEV/wodiq-site", "Tuinstra-DEV/marcel-site",
        "Tuinstra-DEV/tuinstra-site"]
    if any(type(old.get(key)) is not type(value) or old.get(key) != value
           for key, value in exact.items()):
        raise Error("installed admission policy differs from reviewed bounds")
    old_refs = refs(old)
    new_ref = REF_PREFIX + new_sha
    known = {REF_PREFIX + s for s in KNOWN_OLD_SHAS}
    if new_ref in old_refs:
        if old_refs[-1] != new_ref or not 1 <= len(old_refs[:-1]) <= 2 or \
                not set(old_refs[:-1]).issubset(known):
            raise Error("installed three-reference policy is not the reviewed transition")
        return data, old_refs, old_refs, True
    if not set(old_refs).issubset(known) or len(old_refs) > 2:
        raise Error("existing workflow pins differ from reviewed refs")
    new_refs = (*old_refs, new_ref)
    text = data.decode("utf-8")
    # TOML root fields must precede the repository-ID table. Preserve every other byte.
    field = "org_workflow_ref" if "org_workflow_ref" in old else "org_workflow_refs"
    pattern = rf"(?m)^{field}\s*=\s*[^\n]*\n"
    lines = re.findall(pattern, text)
    if len(lines) != 1 or (field == "org_workflow_refs" and
                           not lines[0].rstrip().endswith("]")):
        raise Error("organization workflow field is not a single root TOML line")
    replacement = "org_workflow_refs = " + json.dumps(new_refs) + "\n"
    transformed, count = re.subn(pattern, lambda _match: replacement, text)
    if count != 1:
        raise Error("organization workflow field is not a single root TOML line")
    candidate = transformed.encode("utf-8")
    expected = {key: value for key, value in old.items() if key != field}
    expected["org_workflow_refs"] = list(new_refs)
    if parse_config(candidate) != expected:
        raise Error("workflow config transformation changed unrelated policy")
    return candidate, old_refs, new_refs, False


class GitHubGroup:
    """Fixed API endpoint; exceptions never carry authorization headers to output."""

    def __init__(self, group_id: int, token: bytes):
        try:
            value = token.decode("utf-8").strip()
            if not value or "\n" in value or "\r" in value:
                raise ValueError
        except (UnicodeError, ValueError) as exc:
            raise Error("organization credential is invalid") from exc
        self.authorization = "Bearer " + value
        self.url = f"https://api.github.com/orgs/{ORG}/actions/runner-groups/{group_id}"
        self.group_id = group_id

    def request(self, url: str, method: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": self.authorization, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.HTTPHandler(),
                                             urllib.request.HTTPSHandler(),
                                             urllib.request.ProxyHandler({}), base.NoRedirect())
        try:
            with opener.open(req, timeout=20) as response:
                if response.status != 200:
                    raise ValueError
                body = response.read(1024 * 1024 + 1)
                if len(body) > 1024 * 1024:
                    raise ValueError
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise Error("GitHub runner group request failed") from None

    def verify(self, expected: tuple[str, ...]) -> None:
        group = self.request(self.url, "GET")
        policy = {"id": self.group_id, "name": GROUP_NAME, "visibility": "selected",
                  "default": False, "inherited": False,
                  "allows_public_repositories": False, "restricted_to_workflows": True}
        mismatches = [key for key, value in policy.items()
                      if type(group.get(key)) is not type(value) or group.get(key) != value]
        selected_refs = group.get("selected_workflows")
        if not isinstance(selected_refs, list) or \
                not all(isinstance(ref, str) for ref in selected_refs) or \
                len(selected_refs) != len(expected) or set(selected_refs) != set(expected):
            mismatches.append("selected_workflows")
        if mismatches:
            raise Error("organization runner group policy differs from reviewed settings; fields=" +
                        ",".join(mismatches))
        selection = self.request(self.url + "/repositories?per_page=100&page=1", "GET")
        items = selection.get("repositories")
        if selection.get("total_count") != len(base.REPOSITORIES) or \
                not isinstance(items, list) or len(items) != len(base.REPOSITORIES):
            raise Error("organization repository selection differs")
        actual = {}
        for item in items:
            if not isinstance(item, dict) or item.get("private") is not True or \
                    not isinstance(item.get("full_name"), str) or \
                    type(item.get("id")) is not int or item["full_name"] in actual:
                raise Error("organization repository selection differs")
            actual[item["full_name"]] = item["id"]
        if actual != base.REPOSITORIES:
            raise Error("organization repository selection differs")

    def patch(self, before: tuple[str, ...], after: tuple[str, ...]) -> None:
        self.verify(before)
        self.request(self.url, "PATCH", {"selected_workflows": list(after)})
        self.verify(after)


def verify_sources(stage: Path, layout: Layout) -> tuple[dict, dict, dict]:
    for path in (stage, stage.parent, layout.config.parent, layout.manager.parent,
                 layout.helper.parent, layout.unit.parent, layout.dropin.parent,
                 layout.org_token.parent):
        base.checked_dir(path)
    activation, activation_info = base.read_file(stage / "activate-org-runner-routing.py")
    if base.sha(activation) != ACTIVATION_SHA or activation_info.st_gid != 0:
        raise Error("staged activation primitives differ from reviewed source")
    source, source_info = base.read_file(stage / "ci_runner_manager.py")
    if base.sha(source) != NEW_MANAGER_SHA or source_info.st_gid != 0 or \
            stat.S_IMODE(source_info.st_mode) not in {0o644, 0o755}:
        raise Error("staged manager differs from reviewed source")
    old, manager_info = base.read_file(layout.manager, mode=0o755)
    config, config_info = base.read_file(layout.config, mode=0o640)
    helper, helper_info = base.read_file(layout.helper, mode=0o755)
    unit, unit_info = base.read_file(layout.unit, mode=0o644)
    token, token_info = base.read_file(layout.org_token, mode=0o600)
    dropin, dropin_info = base.read_file(layout.dropin, mode=0o644)
    if base.sha(old) not in {OLD_MANAGER_SHA, NEW_MANAGER_SHA} or \
            base.sha(helper) != base.HELPER_SHA or base.sha(unit) != base.UNIT_SHA or \
            dropin != base.DROPIN_BYTES or any(info.st_gid != 0 for info in
                (manager_info, helper_info, unit_info, token_info, dropin_info)) or \
            config_info.st_gid != base.grp.getgrnam("ci-runner-manager").gr_gid:
        raise Error("installed runner source or metadata differs")
    if layout.drain.exists() or layout.drain.is_symlink():
        raise Error("runtime drain drop-in already exists")
    return {"manager": old, "config": config}, {"manager": source}, \
           {"manager": manager_info, "config": config_info, "token": token}


def preflight(stage: Path, new_sha: str, group_id: int, layout: Layout = HOST,
              command: Command = base.run, group_factory=GitHubGroup):
    original, replacement, infos = verify_sources(stage, layout)
    candidate, before, after, refs_applied = candidate_config(original["config"], new_sha, group_id)
    if not refs_applied or before != after or len(after) != 3 or \
            set(after) != {REF_PREFIX + sha for sha in (*KNOWN_OLD_SHAS, ADMITTED_WORKFLOW_SHA)}:
        raise Error("installed three-reference policy is not the reviewed manager-only transition")
    replacement["config"] = candidate
    installed_sha = base.sha(original["manager"])
    if installed_sha not in {OLD_MANAGER_SHA, NEW_MANAGER_SHA}:
        raise Error("installed manager version differs from reviewed transition")
    applied = installed_sha == NEW_MANAGER_SHA
    base.verify_unit(layout, command, org=True)
    group = group_factory(group_id, infos["token"])
    group.verify(before)
    return original, replacement, infos, group, before, after, applied


def assert_idle(layout: Layout, command: Command) -> None:
    if any(not stat.S_ISDIR(path.lstat().st_mode) for path in (layout.state, layout.overlay)):
        raise Error("runner state or overlay directory is unsafe")
    base.assert_drained(layout, command)
    base.no_helpers(command)


def assert_reconcile_safe(layout: Layout, now: int) -> None:
    """Avoid invoking reconcile when a live lease may cross its fixed TTL."""
    if not stat.S_ISDIR(layout.state.lstat().st_mode):
        raise Error("runner state directory is unsafe")
    for path in layout.state.glob("lease-*.json"):
        try:
            info = path.lstat()
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or \
                        (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode) != \
                        (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_gid,
                         opened.st_mode):
                    raise ValueError
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    document = json.loads(stream.read(1024 * 1024 + 1))
            finally:
                os.close(fd)
            if not isinstance(document, dict) or document.get("lease") != path.name[6:-5] or \
                    type(document.get("launched_at")) is not int or document["launched_at"] <= 0 or \
                    document["launched_at"] + 7200 <= now + 240:
                raise ValueError
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise Error("runner lease is unreadable, malformed, or near expiry") from None


def drain(layout: Layout, seconds: int, command: Command, pause=time.sleep) -> tuple[int, int]:
    if not 60 <= seconds <= 7200 or not base.active(SERVICE, command):
        raise Error("drain window or manager state is invalid")
    command(["systemctl", "stop", SERVICE])
    base.checked_dir(layout.drain.parent.parent)
    layout.drain.parent.mkdir(mode=0o755, exist_ok=True)
    identity = base.create_file(layout.drain, base.DRAIN_BYTES)
    try:
        command(["systemctl", "daemon-reload"])
        checks = {key: command(["systemctl", "show", SERVICE, f"--property={key}", "--value"]).stdout.strip()
                  for key in ("Type", "Restart", "ExecStart", "DropInPaths")}
        if checks["Type"] != "oneshot" or checks["Restart"] != "no" or \
                checks["ExecStart"].count("argv[]=") != 1 or \
                "argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml reconcile" not in checks["ExecStart"] or \
                set(checks["DropInPaths"].split()) != {str(layout.dropin), str(layout.drain)} or \
                base.credential_pairs(command) != base.ORG_CREDENTIALS:
            raise Error("runtime reconcile-only manager or credentials are not verified")
        deadline = time.monotonic() + seconds
        while True:
            assert_reconcile_safe(layout, int(time.time()))
            # The installed manager can fail to verify an already-assigned
            # cross-pin job. Keep admission closed and wait for guest cleanup;
            # only the independent idle checks may authorize the swap.
            command(["systemctl", "start", SERVICE], timeout=180, check=False)
            try:
                assert_idle(layout, command)
                return identity
            except Error:
                if time.monotonic() >= deadline:
                    raise Error("runner drain timed out")
                pause(min(15.0, max(0.0, deadline - time.monotonic())))
    except Exception:
        base.remove_own_file(layout.drain, identity, base.DRAIN_BYTES)
        command(["systemctl", "daemon-reload"])
        raise


def save_backup(layout: Layout, original: dict, replacement: dict, infos: dict,
                group_id: int, before: tuple[str, ...], after: tuple[str, ...]) -> Path:
    folder = base.backup(layout, original, replacement, infos)
    manifest, info = base.read_file(folder / "manifest.json", mode=0o600)
    record = json.loads(manifest)
    record["group"] = {"id": group_id, "before": list(before), "after": list(after)}
    base.atomic_write(folder / "manifest.json", json.dumps(record, sort_keys=True).encode(), info)
    return folder


def read_backup(layout: Layout, backup_id: str):
    if re.fullmatch(BACKUP_PATTERN, backup_id) is None:
        raise Error("backup identifier is invalid")
    folder = layout.backup_root / backup_id
    base.checked_dir(layout.backup_root)
    base.checked_dir(folder)
    manifest = json.loads(base.read_file(folder / "manifest.json", mode=0o600)[0])
    group = manifest.get("group")
    if not isinstance(group, dict) or type(group.get("id")) is not int or \
            group["id"] <= 1 or not isinstance(group.get("before"), list) or \
            not isinstance(group.get("after"), list):
        raise Error("backup group manifest is invalid")
    before, after = tuple(group["before"]), tuple(group["after"])
    if len(before) != 3 or before != after or \
            set(before) != {REF_PREFIX + sha for sha in (*KNOWN_OLD_SHAS, ADMITTED_WORKFLOW_SHA)} or \
            refs({"org_workflow_refs": list(before)}) != before:
        raise Error("backup workflow transition is invalid")
    # Inspect both originals and installed files before any mutation.
    for key, path in (("manager", layout.manager), ("config", layout.config)):
        record = manifest.get(key)
        old = base.read_file(folder / key, mode=0o600)[0]
        current, info = base.read_file(path)
        if not isinstance(record, dict) or base.sha(old) != record.get("old") or \
                base.sha(current) != record.get("new") or \
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != \
                (record.get("uid"), record.get("gid"), record.get("mode")):
            raise Error("backup or installed files changed")
    if manifest["manager"]["old"] != OLD_MANAGER_SHA or \
            manifest["manager"]["new"] != NEW_MANAGER_SHA or \
            manifest["config"]["old"] != manifest["config"]["new"] or \
            refs(parse_config(base.read_file(folder / "config", mode=0o600)[0])) != before or \
            refs(parse_config(base.read_file(layout.config)[0])) != after or \
            parse_config(base.read_file(folder / "config", mode=0o600)[0]).get("org_runner_group_id") != group["id"] or \
            parse_config(base.read_file(layout.config)[0]).get("org_runner_group_id") != group["id"]:
        raise Error("backup source or installed reference policy changed")
    saved_config = base.read_file(folder / "config", mode=0o600)[0]
    candidate, checked_before, checked_after, refs_applied = candidate_config(
        saved_config, ADMITTED_WORKFLOW_SHA, group["id"])
    if candidate != saved_config or not refs_applied or \
            checked_before != before or checked_after != after:
        raise Error("backup admission policy differs from reviewed bounds")
    return folder, group, manifest


def transition(stage: Path, new_sha: str, group_id: int, seconds: int,
               layout: Layout = HOST, command: Command = base.run,
               group_factory=GitHubGroup) -> tuple[str, str | None]:
    activation_lock = base.lock(layout.lock)
    drain_identity = None
    folder = None
    try:
        original, replacement, infos, group, before, after, applied = preflight(
            stage, new_sha, group_id, layout, command, group_factory)
    except Exception:
        os.close(activation_lock)
        raise
    try:
        if applied:
            return "already_applied", None
        drain_identity = drain(layout, seconds, command)
        command(["systemctl", "stop", SERVICE])
        command(["systemctl", "stop", SOCKET])
        if base.active(SERVICE, command) or base.active(SOCKET, command):
            raise Error("admission services did not stop")
        assert_idle(layout, command)
        helper_lock = base.lock(layout.helper_lock)
        try:
            assert_idle(layout, command)
            if base.read_file(layout.manager)[0] != original["manager"] or \
                    base.read_file(layout.config)[0] != original["config"]:
                raise Error("installed runner changed during drain")
            group.verify(before)
            folder = save_backup(layout, original, replacement, infos, group_id, before, after)
            base.atomic_write(layout.manager, replacement["manager"], infos["manager"])
            if base.sha(base.read_file(layout.manager)[0]) != base.sha(replacement["manager"]):
                raise Error("installed file readback failed")
        finally:
            os.close(helper_lock)
        base.remove_own_file(layout.drain, drain_identity, base.DRAIN_BYTES)
        drain_identity = None
        command(["systemctl", "daemon-reload"])
        base.verify_unit(layout, command, org=True)
        assert_idle(layout, command)
        group.verify(after)
        command(["systemctl", "start", SOCKET])
        command(["systemctl", "start", SERVICE])
        if not base.active(SOCKET, command) or not base.active(SERVICE, command):
            raise Error("updated manager did not become active")
        return "applied", folder.name
    except Exception as failure:
        reason = str(failure) if isinstance(failure, Error) else type(failure).__name__
        try:
            command(["systemctl", "stop", SERVICE], check=False)
            command(["systemctl", "stop", SOCKET], check=False)
            if base.active(SERVICE, command) or base.active(SOCKET, command):
                raise Error("admission services did not stop for recovery")
            if folder is not None:
                assert_idle(layout, command)
                helper_lock = base.lock(layout.helper_lock)
                try:
                    assert_idle(layout, command)
                    group.verify(before)
                    base.restore(layout, folder, partial=True)
                finally:
                    os.close(helper_lock)
            elif original is not None and (base.read_file(layout.manager)[0] != original["manager"] or
                                            base.read_file(layout.config)[0] != original["config"]):
                raise Error("installed runner changed before backup")
            if drain_identity is not None:
                base.remove_own_file(layout.drain, drain_identity, base.DRAIN_BYTES)
            command(["systemctl", "daemon-reload"])
            base.verify_unit(layout, command, org=True)
            if group is not None and before is not None:
                group.verify(before)
            command(["systemctl", "start", SOCKET])
            command(["systemctl", "start", SERVICE])
            if not base.active(SOCKET, command) or not base.active(SERVICE, command):
                raise Error("original admission services did not restart")
        except Exception as recovery:
            raise Error(f"transition failed; admission remains stopped; backup={folder.name if folder else 'none'}; "
                        f"failure={reason}; recovery={type(recovery).__name__}") from None
        raise Error(f"transition aborted; original admission restored; failure={reason}") from None
    finally:
        os.close(activation_lock)


def rollback(backup_id: str, seconds: int, layout: Layout = HOST,
             command: Command = base.run, group_factory=GitHubGroup) -> None:
    activation_lock = base.lock(layout.lock)
    drain_identity = None
    try:
        folder, record, _ = read_backup(layout, backup_id)
        source, _ = base.read_file(layout.manager, mode=0o755)
        if base.sha(source) != NEW_MANAGER_SHA:
            raise Error("installed manager is not the reviewed new version")
        helper, helper_info = base.read_file(layout.helper, mode=0o755)
        unit, unit_info = base.read_file(layout.unit, mode=0o644)
        dropin, dropin_info = base.read_file(layout.dropin, mode=0o644)
        if base.sha(helper) != base.HELPER_SHA or base.sha(unit) != base.UNIT_SHA or \
                dropin != base.DROPIN_BYTES or any(info.st_gid != 0 for info in
                (helper_info, unit_info, dropin_info)):
            raise Error("installed support policy changed")
        base.verify_unit(layout, command, org=True)
        token = base.read_file(layout.org_token, mode=0o600)[0]
        group = group_factory(record["id"], token)
        before, after = tuple(record["before"]), tuple(record["after"])
        group.verify(after)
    except Exception:
        os.close(activation_lock)
        raise
    try:
        drain_identity = drain(layout, seconds, command)
    except Exception as failure:
        # Drain never changed the installed version or runner group. Restore
        # normal admission even if a guest still owns a live lease.
        try:
            if not base.active(SERVICE, command):
                command(["systemctl", "start", SERVICE])
            if not base.active(SOCKET, command):
                command(["systemctl", "start", SOCKET])
            if not base.active(SERVICE, command) or not base.active(SOCKET, command):
                raise Error("admission services did not restart")
        except Exception:
            os.close(activation_lock)
            raise Error("rollback drain failed; admission could not be restored") from None
        os.close(activation_lock)
        raise Error(f"rollback drain aborted; installed admission unchanged; reason={type(failure).__name__}") from None
    try:
        command(["systemctl", "stop", SERVICE])
        command(["systemctl", "stop", SOCKET])
        if base.active(SERVICE, command) or base.active(SOCKET, command):
            raise Error("admission services did not stop")
        assert_idle(layout, command)
        helper_lock = base.lock(layout.helper_lock)
        try:
            assert_idle(layout, command)
            read_backup(layout, backup_id)
            group.verify(before)
            base.restore(layout, folder, partial=False)
        finally:
            os.close(helper_lock)
        base.remove_own_file(layout.drain, drain_identity, base.DRAIN_BYTES)
        drain_identity = None
        command(["systemctl", "daemon-reload"])
        base.verify_unit(layout, command, org=True)
        group.verify(before)
        command(["systemctl", "start", SOCKET])
        command(["systemctl", "start", SERVICE])
        if not base.active(SOCKET, command) or not base.active(SERVICE, command):
            raise Error("restored manager did not become active")
    except Exception as failure:
        # A failed rollback is deliberately left closed. Never claim recovery.
        command(["systemctl", "stop", SERVICE], check=False)
        command(["systemctl", "stop", SOCKET], check=False)
        stopped = not base.active(SERVICE, command) and not base.active(SOCKET, command)
        state = "admission stopped" if stopped else "admission stop unverified"
        raise Error(f"rollback incomplete; {state}; reason={type(failure).__name__}") from None
    finally:
        os.close(activation_lock)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, default=Path("/root/ci-runner-org-routing-stage"))
    parser.add_argument("--workflow-sha")
    parser.add_argument("--group-id", type=int)
    parser.add_argument("--max-drain-seconds", type=int, default=3600)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--rollback", metavar="BACKUP_ID")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        parser.error("run as root via the named operator account's sudo")
    if not 60 <= args.max_drain_seconds <= 7200:
        parser.error("drain window must be 60–7200 seconds")
    if args.rollback is None and (args.workflow_sha is None or args.group_id is None):
        parser.error("--workflow-sha and --group-id are required")
    os.environ["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    try:
        base.checked_dir(Path(__file__).parent)
        base.read_file(Path(__file__))
        if base.sha(base.read_file(_source)[0]) != ACTIVATION_SHA:
            raise Error("reviewed activation primitives changed")
        if args.rollback is not None:
            rollback(args.rollback, args.max_drain_seconds)
            result = {"status": "rolled_back", "backup_id": args.rollback}
        elif args.apply:
            status, backup_id = transition(args.stage, args.workflow_sha, args.group_id,
                                           args.max_drain_seconds)
            result = {"status": status, "backup_id": backup_id}
        else:
            _, _, _, _, _, _, applied = preflight(args.stage, args.workflow_sha, args.group_id)
            result = {"status": "already_applied" if applied else "preflight_passed"}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Error, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        message = str(exc) if isinstance(exc, Error) else type(exc).__name__
        print(json.dumps({"status": "failed", "reason": message}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
