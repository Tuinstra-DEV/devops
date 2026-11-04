#!/bin/sh
# Fixed-purpose interactive sudo entrypoint. No paths, refs or commands from argv/env.
# The old DEV-14 role requests DO NOT automatically fall back after a role repin.
# Activate only without open PHP/CVE hotfixes and while all five affected repos are quiet.
exec /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C /usr/bin/python3 -I /dev/fd/3 "$@" 3<<'PY'
from __future__ import annotations

import copy
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
import signal
import stat
import subprocess
import sys
import time
import tomllib
import types
import urllib.error
import urllib.request

SOURCE_COMMIT = "ea86c62eeac615cb292a78bf50995fed81c051ee"
NEW_MANAGER_SHA = "5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3"
OLD_MANAGER_SHA = "54520f176dc5c07adf9493d27f89e549672c27aa6d7fa05f737b6549f0dcb1f3"
UNIT_SHA = "c99fdf23b9c50971be678565db54e85d5227040ff1108ccb3622a9c2ae4d806d"
HELPER_SHA = "fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613"
PREFIX = "Tuinstra-DEV/devops/.github/workflows/"
TRUSTED_REF = PREFIX + "reusable-trusted-verification.yml@" + SOURCE_COMMIT
PRODUCER_REF = PREFIX + "reusable-gate-pr-security.yml@" + SOURCE_COMMIT
LEGACY_METADATA_REF = PREFIX + "reusable-trusted-verification.yml@a8ace2f171d7d127bbfb3a6d6b13f158f461ba98"
TESSERA = "Tuinstra-DEV/sudoku-tessera"
TESSERA_ID = 1179209966
AFFECTED = ("Tuinstra-DEV/gate", "Tuinstra-DEV/tracker", "Tuinstra-DEV/notify", "Tuinstra-DEV/status", TESSERA)
CVE_PATTERN = re.compile(r"CVE-|TRAC[-/]223|NTF[-/](?:19|20)\b|IN[-/](?:43|44|45|46)\b|php.{0,60}(?:security|hotfix)|(?:security|hotfix).{0,60}php", re.I)
SERVICE = "ci-runner-manager.service"
SOCKET = "ci-runner-host-helper.socket"
UID = 0
ANCHOR = Path("/")
STEP = "startup"
DRAIN_BYTES = ("[Service]\nType=oneshot\nExecStart=\nExecStart=/usr/local/bin/ci-runner-manager "
               "--config /etc/ci-runner/manager.toml reconcile\nRestart=no\n").encode()
ORG_DROPIN = b"[Service]\nLoadCredential=org_github_token:/etc/ci-runner/org-github.token\n"


class Refused(RuntimeError):
    pass


class HTTPRefused(Refused):
    def __init__(self, endpoint, repository, status, permission_denial=False):
        self.permission_denial = permission_denial
        # Only reviewed endpoint categories, public repository identifiers and
        # an integer status may enter an operator-facing exception value.
        label = endpoint + (" " + repository if repository in (*AFFECTED, "Tuinstra-DEV/devops") else "")
        super().__init__(label + " HTTP " + str(status if type(status) is int else "unknown"))


def step(name):
    global STEP
    STEP = name


def report_failure(failure):
    # Never render an unexpected exception value, chain, source line or locals:
    # parsing/network exceptions can contain config, URLs or response content.
    print("DEV-54 failed: " + type(failure).__name__ + " at step " + STEP + ".", file=sys.stderr)


def require(ok, reason):
    if not ok:
        raise Refused(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Layout:
    manager: Path = Path("/usr/local/bin/ci-runner-manager")
    config: Path = Path("/etc/ci-runner/manager.toml")
    helper: Path = Path("/usr/local/libexec/ci-runner-host-helper")
    unit: Path = Path("/etc/systemd/system/ci-runner-manager.service")
    org_dropin: Path = Path("/etc/systemd/system/ci-runner-manager.service.d/20-org-credential.conf")
    repo_token: Path = Path("/etc/ci-runner/github.token")
    org_token: Path = Path("/etc/ci-runner/org-github.token")
    drain: Path = Path("/run/systemd/system/ci-runner-manager.service.d/90-dev54-drain.conf")
    state: Path = Path("/var/lib/ci-runner-manager/state")
    overlay: Path = Path("/var/lib/ci-runner/overlay")
    backup: Path = Path("/var/backups/dev54-activation")
    lock: Path = Path("/run/dev54-activation/lock")
    helper_lock: Path = Path("/run/lock/ci-runner-host-helper.lock")


def secure_dir(path):
    require(path.is_absolute() and path.is_relative_to(ANCHOR), "directory outside trusted root")
    for item in (path, *path.parents):
        info = item.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == UID and not info.st_mode & 0o022,
                "unsafe root directory")
        if item == ANCHOR:
            break


def private_dir(path, mode=0o700):
    secure_dir(path.parent)
    if not path.exists():
        path.mkdir(mode=mode)
    secure_dir(path)
    require(stat.S_IMODE(path.stat().st_mode) == mode, "directory mode differs")


def read_file(path, mode=None):
    secure_dir(path.parent)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == UID and info.st_nlink == 1 and
            not info.st_mode & 0o022 and (mode is None or stat.S_IMODE(info.st_mode) == mode),
            "unsafe protected file")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        actual = os.fstat(fd)
        require((info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid) ==
                (actual.st_dev, actual.st_ino, actual.st_mode, actual.st_uid, actual.st_gid),
                "protected file changed during read")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        require(len(data) <= 2 * 1024 * 1024, "protected file too large")
        return data, actual
    finally:
        os.close(fd)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, data, mode=0o600):
    secure_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    os.fchmod(fd, mode)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_dir(path.parent)


def replace(path, data, info):
    # Verify the original inode again before atomic replacement, preserving ownership/mode.
    current, actual = read_file(path)
    require((actual.st_dev, actual.st_ino, actual.st_uid, actual.st_gid, actual.st_mode) ==
            (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode), "replacement target changed")
    temporary = path.with_name(".dev54-" + secrets.token_hex(12))
    try:
        write_new(temporary, data, stat.S_IMODE(info.st_mode))
        os.chown(temporary, info.st_uid, info.st_gid)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def lock(path, helper=False):
    if helper:
        # /run/lock is root-owned sticky on Ubuntu. Never trust a precreated user file.
        secure_dir(path.parent.parent)
        parent = path.parent.lstat()
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == UID and
                (not parent.st_mode & 0o022 or parent.st_mode & stat.S_ISVTX), "unsafe helper lock directory")
    else:
        private_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == UID and info.st_nlink == 1 and
            not info.st_mode & 0o022, "unsafe lock file")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def run(args, check=True, timeout=180):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    require(not check or result.returncode == 0, "system command failed; output suppressed")
    return result.stdout.strip()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch(url, token=None, *, endpoint="manager-source", repository=""):
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            require(response.status == 200, endpoint + " unexpected HTTP status")
            data = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as failure:
        status = failure.code
        headers = failure.headers or {}
        permission_denial = status in (403, 404) and not (
            headers.get("X-RateLimit-Remaining") == "0" or headers.get("Retry-After") is not None)
        failure.close()
        raise HTTPRefused(endpoint, repository, status, permission_denial) from None
    except (urllib.error.URLError, TimeoutError) as failure:
        raise Refused(endpoint + " transport failure (" + type(failure).__name__ + ")") from None
    require(len(data) <= 1024 * 1024, "read-only source/API result too large")
    return data


def module_from_source(source):
    require(digest(source) == NEW_MANAGER_SHA, "manager source SHA-256 differs")
    module = types.ModuleType("dev54_pinned_manager")
    sys.modules[module.__name__] = module
    module.__file__ = "/usr/local/bin/ci-runner-manager"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def source_module():
    source = fetch("https://raw.githubusercontent.com/Tuinstra-DEV/devops/" + SOURCE_COMMIT +
                   "/runner/manager/ci_runner_manager.py")
    return source, module_from_source(source)


def toml_value(value):
    if type(value) is bool:
        return str(value).lower()
    if type(value) in (str, int):
        return json.dumps(value)
    if type(value) is list:
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if type(value) is dict:
        return "{ " + ", ".join(json.dumps(k) + " = " + toml_value(v) for k, v in value.items()) + " }"
    raise Refused("unsupported TOML value; no policy was changed")


def serialize(cfg):
    data = ("\n".join(json.dumps(k) + " = " + toml_value(v) for k, v in cfg.items()) + "\n").encode()
    require(tomllib.loads(data.decode()) == cfg, "candidate TOML does not round-trip")
    return data


def candidate(cfg, module):
    module.validate_org_config(cfg)
    before = list(module.configured_trusted_workflow_refs(cfg))
    require(len(before) == 4 and before[3] == LEGACY_METADATA_REF,
            "expected four retained trusted pins including DEV-14 metadata; manual review required")
    require(set(cfg.get("org_execution_roles", {})) == {"metadata-preflight", "pr-security-producer"},
            "both existing DEV-14 roles are required")
    result = copy.deepcopy(cfg)
    result.pop("org_workflow_ref", None)
    result["org_workflow_refs"] = before + [TRUSTED_REF]
    require(TESSERA not in cfg["org_repository_ids"] and TESSERA not in cfg["repositories"],
            "unexpected prior Tessera admission")
    result["org_repository_ids"][TESSERA] = TESSERA_ID
    result["repositories"].append(TESSERA)
    transitions = {}
    for role, ref in (("metadata-preflight", TRUSTED_REF), ("pr-security-producer", PRODUCER_REF)):
        previous = result["org_execution_roles"][role]["workflow_ref"]
        transitions[role] = {"from": previous, "to": ref}
        result["org_execution_roles"][role]["workflow_ref"] = ref
        # Actors, repositories, events and all other role restrictions are byte-for-byte values.
        require({k: v for k, v in result["org_execution_roles"][role].items() if k != "workflow_ref"} ==
                {k: v for k, v in cfg["org_execution_roles"][role].items() if k != "workflow_ref"},
                "role controls changed")
    require(set(before).issubset(result["org_workflow_refs"]) and len(result["org_workflow_refs"]) == 5,
            "trusted pins were removed or extended beyond five")
    module.validate_org_config(result)
    return result, transitions


def policy(cfg, module):
    return {"selected_workflows": list(module.configured_org_workflow_refs(cfg)),
            "selected_repositories": cfg["org_repository_ids"], "roles": cfg["org_execution_roles"],
            "trusted_pins": list(module.configured_trusted_workflow_refs(cfg))}


class GitHub:
    def __init__(self, layout, cfg, module):
        self.module = module
        self.repo_token = read_file(layout.repo_token, 0o600)[0].decode().strip()
        self.org_token = read_file(layout.org_token, 0o600)[0].decode().strip()
        require(all(t and "\n" not in t for t in (self.repo_token, self.org_token)), "invalid credential binding")

    def get(self, path):
        endpoint = "open-pr-inventory" if "/pulls?" in path else "run-inventory"
        repository = "/".join(path.split("/")[2:4])
        require(repository in AFFECTED, "unexpected preflight repository")
        options = {"endpoint": endpoint, "repository": repository}
        url = "https://api.github.com" + path
        try:
            data = fetch(url, self.repo_token, **options)
        except HTTPRefused as failure:
            # Both credentials are already provisioned. A successful identical
            # GET proves the org credential's read access; no repository/check
            # is skipped and no scopes, credentials or host policy are changed.
            # Rate limits, server and transport failures never switch tokens.
            if not failure.permission_denial or self.repo_token == self.org_token:
                raise
            data = fetch(url, self.org_token, **options)
        return json.loads(data)

    def group(self, cfg):
        step("runner-group")
        client = self.module.GitHubClient(self.org_token)
        try:
            client.verify_org_group(cfg)  # GET-only exact group/ref/private repository equality.
        except self.module.RunnerError as failure:
            # The manager wraps HTTP/transport errors; inspect only their types
            # and numeric status, never its exception message or response body.
            cause = failure.__cause__
            if isinstance(cause, urllib.error.HTTPError):
                raise Refused("runner-group HTTP " + str(cause.code)) from None
            if isinstance(cause, (urllib.error.URLError, TimeoutError)):
                raise Refused("runner-group transport failure (" + type(cause).__name__ + ")") from None
            raise Refused("runner-group policy/API validation failed") from None

    def quiet(self):
        step("run-inventory")
        for repo in AFFECTED:
            for status in ("queued", "in_progress"):
                value = self.get(f"/repos/{repo}/actions/runs?status={status}&per_page=1")
                require(type(value) is dict and type(value.get("total_count")) is int and value["total_count"] == 0 and value.get("workflow_runs") == [],
                        "affected repository has queued/active runs; activation postponed")

    def no_open_cve_hotfixes(self):
        step("open-pr-inventory")
        # Historical CVE PRs are merged; gate on today's open inventory instead.
        for repo in AFFECTED:
            prs = self.get(f"/repos/{repo}/pulls?state=open&per_page=100&page=1")
            require(type(prs) is list and len(prs) < 100 and all(type(pr) is dict for pr in prs),
                    "ambiguous open-PR inventory")
            for pr in prs:
                text = " ".join(str(pr.get(k) or "") for k in ("title",))
                text += " " + str((pr.get("head") or {}).get("ref") or "")
                require(not CVE_PATTERN.search(text), "open CVE/security hotfix PR; activation postponed")


def check_unit(layout, command, draining=False):
    require(digest(read_file(layout.unit, 0o644)[0]) == UNIT_SHA and
            read_file(layout.org_dropin, 0o644)[0] == ORG_DROPIN and
            digest(read_file(layout.helper, 0o755)[0]) == HELPER_SHA, "unit/helper source drift")
    expected = {"FragmentPath": str(layout.unit), "Type": "oneshot" if draining else "simple",
                "Restart": "no" if draining else "always", "User": "ci-runner-manager", "Group": "ci-runner-manager"}
    for key, value in expected.items():
        require(command(["systemctl", "show", SERVICE, "--property=" + key, "--value"]) == value,
                "effective manager unit differs")
    start = command(["systemctl", "show", SERVICE, "--property=ExecStart", "--value"])
    suffix = "reconcile" if draining else "daemon --interval 30"
    require(start.count("argv[]=") == 1 and
            "argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml " + suffix in start,
            "effective manager command differs")
    dropins = command(["systemctl", "show", SERVICE, "--property=DropInPaths", "--value"]).split()
    require(set(dropins) == {str(layout.org_dropin)} | ({str(layout.drain)} if draining else set()),
            "unreviewed manager drop-ins")
    credentials = json.loads(command(["busctl", "--json=short", "get-property", "org.freedesktop.systemd1",
        "/org/freedesktop/systemd1/unit/ci_2drunner_2dmanager_2eservice",
        "org.freedesktop.systemd1.Service", "LoadCredential"]))
    require(credentials.get("type") == "a(ss)" and sorted(credentials.get("data", [])) ==
            sorted([["github_token", "/etc/ci-runner/github.token"],
                    ["org_github_token", "/etc/ci-runner/org-github.token"]]), "credential unit bindings differ")


def idle(layout, command):
    require(all(stat.S_ISDIR(p.lstat().st_mode) and not p.lstat().st_mode & 0o022
                for p in (layout.state, layout.overlay)), "unsafe state/storage directory")
    domains = command(["virsh", "--connect", "qemu:///system", "list", "--all", "--name"]).splitlines()
    require(not any(name.startswith("sanctuary-ci-") for name in domains), "runner VM still exists")
    require(not list(layout.overlay.iterdir()), "runner overlay remains")
    require(not any(p.name.startswith(("lease-", "cleanup-")) or p.name.endswith(".tmp")
                    for p in layout.state.iterdir()), "lease or cleanup obligation remains")
    helpers = command(["systemctl", "list-units", "--all", "--type=service",
                       "--state=activating,running,reloading,deactivating", "--plain", "--no-legend",
                       "ci-runner-host-helper@*.service"])
    require(not helpers, "host helper is still active")


def reconcile_safe(layout, now):
    # Reconcile must never kill a healthy VM because its two-hour lease is expiring.
    for path in layout.state.glob("lease-*.json"):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o022, "unsafe runner lease")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            actual = os.fstat(stream.fileno())
            require((info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode) ==
                    (actual.st_dev, actual.st_ino, actual.st_uid, actual.st_gid, actual.st_mode), "lease changed during read")
            data = stream.read(1024 * 1024 + 1)
            require(len(data) <= 1024 * 1024, "oversized runner lease")
            lease = json.loads(data)
        require(lease.get("lease") == path.name[6:-5] and type(lease.get("launched_at")) is int and
                lease["launched_at"] > 0 and lease["launched_at"] + 7200 > now + 240,
                "runner lease is malformed/near expiry; no forced cleanup")


def pause_manager(layout, command):
    require(not layout.drain.exists() and not layout.drain.is_symlink(), "drain already exists")
    command(["systemctl", "stop", SERVICE])
    if not layout.drain.parent.exists():
        secure_dir(layout.drain.parent.parent)
        layout.drain.parent.mkdir(mode=0o755)
    write_new(layout.drain, DRAIN_BYTES, 0o644)
    command(["systemctl", "daemon-reload"])
    check_unit(layout, command, draining=True)


def drain(layout, command, pause=time.sleep, seconds=3600):
    deadline = time.monotonic() + seconds
    while True:
        reconcile_safe(layout, int(time.time()))
        command(["systemctl", "start", SERVICE], check=False)
        try:
            idle(layout, command)
            return
        except Refused:
            require(time.monotonic() < deadline, "natural drain timed out; admission stays paused")
            pause(min(15, max(0, deadline - time.monotonic())))


def restart(layout, command):
    require(read_file(layout.drain, 0o644)[0] == DRAIN_BYTES, "drain changed")
    layout.drain.unlink()
    sync_dir(layout.drain.parent)
    command(["systemctl", "daemon-reload"])
    check_unit(layout, command)
    command(["systemctl", "restart", SERVICE])
    for unit in (SERVICE, SOCKET):
        require(command(["systemctl", "is-active", unit]) == "active", "manager/socket not active")
    require(command(["systemctl", "show", SERVICE, "--property=ExecMainStatus", "--value"]) == "0",
            "manager exit status is nonzero")


def checkpoint(folder, document):
    path = folder / "manifest.json"
    data = json.dumps(document, sort_keys=True).encode()
    if path.exists():
        replace(path, data, read_file(path, 0o600)[1])
    else:
        write_new(path, data)


def make_backup(layout, original, replacement, policies, transitions):
    private_dir(layout.backup)
    folder = layout.backup / (datetime.now(timezone.utc).strftime("dev54-%Y%m%dT%H%M%SZ-") + secrets.token_hex(8))
    folder.mkdir(mode=0o700)
    manifest = {"source_commit": SOURCE_COMMIT, "new_manager_sha256": NEW_MANAGER_SHA, "phase": "prepared", "files": {},
                "role_pin_transitions": transitions, "before_policy": policies[0], "after_policy": policies[1]}
    for name, (data, info) in original.items():
        write_new(folder / name, data)
        manifest["files"][name] = {"old": digest(data), "new": digest(replacement.get(name, data)),
                                  "uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}
    write_new(folder / "policies-before.json", json.dumps(policies[0], sort_keys=True).encode())
    write_new(folder / "policies-after.json", json.dumps(policies[1], sort_keys=True).encode())
    write_new(folder / "new-manager", replacement["manager"])
    checkpoint(folder, manifest)
    pointer = layout.backup / "last-backup"
    if pointer.exists():
        replace(pointer, (folder.name + "\n").encode(), read_file(pointer, 0o600)[1])
    else:
        write_new(pointer, (folder.name + "\n").encode())
    sync_dir(layout.backup)
    print("Backup:", folder.name)
    for role, change in transitions.items():
        print(role + ": " + change["from"] + " -> " + change["to"])
    return folder, manifest


def manual_group(target, confirm):
    # Only the explicitly authorized ref changes are shown, never config/repo tables.
    print("GitHub runnergroep sanctuary-trusted-verification; geen tokens invoeren.")
    print("Doel metadata-rolpin: " + target["roles"]["metadata-preflight"]["workflow_ref"])
    print("Doel producer-rolpin: " + target["roles"]["pr-security-producer"]["workflow_ref"])
    if TESSERA in target["selected_repositories"]:
        print("Behoud de vier oude trusted refs; voeg beide nieuwe refs en Tessera 1179209966 toe.")
        print("Verwijder alleen de vervallen role-only producer-ref uit de eerder getoonde rolpinwissel.")
    else:
        print("Rollback: verwijder beide nieuwe refs, herstel de oude producer-ref en deselecteer Tessera.")
        print("Behoud alle vier oorspronkelijke trusted refs en de overige repositoryselectie.")
    print("Behoud selected/private, public=false, restricted_to_workflows=true.")
    require(confirm() == "GEREED", "operator did not confirm GitHub policy; admission stays paused")


def validate_config(data, module, folder):
    path = folder / (".validate-" + secrets.token_hex(8) + ".toml")
    write_new(path, data)
    try:
        return module.load_config(path)
    finally:
        path.unlink()


def apply(layout=Layout(), command=run, loader=source_module, api_factory=GitHub, confirm=None, pause=time.sleep):
    step("manager-source")
    source, module = loader()
    step("host-files")
    original = {name: read_file(path, mode) for name, path, mode in (
        ("manager", layout.manager, 0o755), ("config", layout.config, 0o640),
        ("unit", layout.unit, 0o644), ("org-dropin", layout.org_dropin, 0o644))}
    require(digest(original["manager"][0]) == OLD_MANAGER_SHA, "unexpected installed manager SHA-256")
    step("configuration")
    cfg = tomllib.loads(original["config"][0].decode())
    require(cfg.get("state_dir") == str(layout.state) and cfg.get("overlay_root") == str(layout.overlay) and
            original["config"][1].st_gid == grp.getgrnam("ci-runner-manager").gr_gid,
            "configured state/storage or config owner differs")
    step("policy-candidate")
    updated, transitions = candidate(cfg, module)
    replacement = {"manager": source, "config": serialize(updated)}
    step("credentials")
    api = api_factory(layout, cfg, module)
    api.no_open_cve_hotfixes()
    api.quiet()
    step("systemd-preflight")
    check_unit(layout, command)
    require(command(["systemctl", "is-active", SERVICE]) == "active", "manager is not active")
    api.group(cfg)
    # Backup before the first service mutation also makes interrupted drain recoverable.
    step("backup")
    folder, manifest = make_backup(layout, original, replacement,
                                    (policy(cfg, module), policy(updated, module)), transitions)
    step("candidate-config-validation")
    validate_config(replacement["config"], module, folder)
    try:
        step("drain")
        pause_manager(layout, command)
        drain(layout, command, pause)
        api.no_open_cve_hotfixes()
        api.quiet()
        manifest["phase"] = "drained"
        checkpoint(folder, manifest)
        step("operator-confirmation")
        manual_group(manifest["after_policy"], confirm)
        api.group(updated)
        api.no_open_cve_hotfixes()
        api.quiet()
        step("install")
        fd = lock(layout.helper_lock, helper=True)
        try:
            idle(layout, command)
            for name, path in (("manager", layout.manager), ("config", layout.config)):
                current, info = read_file(path)
                require(digest(current) == manifest["files"][name]["old"], "installed file drift before swap")
                replace(path, replacement[name], info)
                require(digest(read_file(path)[0]) == manifest["files"][name]["new"], "installed readback differs")
            manifest["phase"] = "installed"
            checkpoint(folder, manifest)
        finally:
            os.close(fd)
        step("restart")
        restart(layout, command)
        api.group(updated)
        time.sleep(2)
        require(command(["systemctl", "is-active", SERVICE]) == "active" and
                command(["systemctl", "show", SERVICE, "--property=NRestarts", "--value"]) == "0",
                "manager did not remain stable")
        manifest["phase"] = "applied"
        checkpoint(folder, manifest)
        print("DEV-54 applied; manager SHA-256 " + NEW_MANAGER_SHA + "; workflow pin " + SOURCE_COMMIT)
    except BaseException:
        command(["systemctl", "stop", SERVICE], check=False)
        print("DEV-54 stopped; admission remains paused. Use --rollback with the recorded backup.")
        raise


def rollback(layout=Layout(), command=run, api_factory=GitHub, confirm=None, pause=time.sleep):
    step("rollback-backup")
    secure_dir(layout.backup)
    name = read_file(layout.backup / "last-backup", 0o600)[0].decode().strip()
    require(re.fullmatch(r"dev54-\d{8}T\d{6}Z-[0-9a-f]{16}", name), "invalid rollback backup name")
    folder = layout.backup / name
    manifest = json.loads(read_file(folder / "manifest.json", 0o600)[0])
    require(manifest.get("source_commit") == SOURCE_COMMIT and manifest.get("new_manager_sha256") == NEW_MANAGER_SHA,
            "backup belongs to another activation version")
    require(manifest.get("phase") in ("prepared", "drained", "installed", "applied"), "backup is not rollback-eligible")
    module = module_from_source(read_file(folder / "new-manager", 0o600)[0])
    original = {}
    for key, path in (("manager", layout.manager), ("config", layout.config),
                      ("unit", layout.unit), ("org-dropin", layout.org_dropin)):
        value = read_file(folder / key, 0o600)[0]
        record = manifest["files"][key]
        current, info = read_file(path)
        require(digest(value) == record["old"] and digest(current) in {record["old"], record["new"]} and
                (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (record["uid"], record["gid"], record["mode"]),
                "backup/installed content or metadata drift; refusing rollback")
        original[key] = value
    require(digest(original["manager"]) == OLD_MANAGER_SHA, "original manager hash differs")
    old_cfg = tomllib.loads(original["config"].decode())
    new_cfg, transitions = candidate(old_cfg, module)
    require(transitions == manifest["role_pin_transitions"] and
            policy(old_cfg, module) == manifest["before_policy"] and policy(new_cfg, module) == manifest["after_policy"],
            "backup policies or role-pin transitions differ")
    step("rollback-credentials")
    api = api_factory(layout, old_cfg, module)
    try:
        step("rollback-drain")
        if layout.drain.exists():
            require(read_file(layout.drain, 0o644)[0] == DRAIN_BYTES, "unknown runtime drain")
            check_unit(layout, command, draining=True)
        else:
            check_unit(layout, command)
            pause_manager(layout, command)
        drain(layout, command, pause)
        step("rollback-confirmation")
        manual_group(manifest["before_policy"], confirm)
        api.group(old_cfg)
        step("rollback-install")
        fd = lock(layout.helper_lock, helper=True)
        try:
            idle(layout, command)
            for key, path in (("manager", layout.manager), ("config", layout.config)):
                current, info = read_file(path)
                require(digest(current) in {manifest["files"][key]["old"], manifest["files"][key]["new"]},
                        "installed content changed during rollback")
                replace(path, original[key], info)
                require(digest(read_file(path)[0]) == manifest["files"][key]["old"], "rollback readback differs")
        finally:
            os.close(fd)
        step("rollback-restart")
        restart(layout, command)
        api.group(old_cfg)
        manifest["phase"] = "rolled_back"
        checkpoint(folder, manifest)
        print("DEV-54 rolled back; manager SHA-256 " + OLD_MANAGER_SHA)
        for role, change in transitions.items():
            print(role + ": " + change["to"] + " -> " + change["from"])
    except BaseException:
        command(["systemctl", "stop", SERVICE], check=False)
        print("DEV-54 rollback stopped; admission remains paused; protected backup preserved.")
        raise


def main():
    step("entrypoint")
    require(os.geteuid() == 0 and sys.argv[1:] in ([], ["--rollback"]), "root and exact no-argument/--rollback invocation required")
    require(sys.stdin.isatty(), "operator TTY required for manual GitHub policy confirmation")
    # Only the root-installed entrypoint is allowed. No user-writable repo/staging imports.
    require(read_file(Path("/usr/local/sbin/dev54-activate.sh"), 0o755)[1].st_gid == 0,
            "install entrypoint root:root 0755 first")
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(Refused("interrupted")))
    step("activation-lock")
    fd = lock(Layout().lock)
    try:
        operation = rollback if sys.argv[1:] else apply
        operation(confirm=lambda: input("Pas de GitHub-selecties aan en typ GEREED: ").strip())
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except Refused as failure:
        print("DEV-54 refused at step " + STEP + ": " + str(failure), file=sys.stderr)
        sys.exit(1)
    except BaseException as failure:
        report_failure(failure)
        sys.exit(1)
PY
