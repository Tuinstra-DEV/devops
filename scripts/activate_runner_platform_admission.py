#!/usr/bin/env python3
"""Safely admit WODIQ Platform to the existing Sanctuary runner manager."""

from __future__ import annotations

import argparse
import fcntl
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
import tomllib
from datetime import datetime, timezone
from typing import Callable
import urllib.error
import urllib.parse
import urllib.request


CONFIG = Path("/etc/ci-runner/manager.toml")
STATE_DIR = Path("/var/lib/ci-runner-manager/state")
OVERLAY_ROOT = Path("/var/lib/ci-runner/overlay")
LOCK_PATH = Path("/run/lock/ci-runner-platform-admission.lock")
SERVICE = "ci-runner-manager.service"
SYSTEMD_CREDENTIAL_PATH = Path("/run/credentials/ci-runner-manager.service/github_token")
SYSTEMD_CREDENTIAL_DIRECTORY = SYSTEMD_CREDENTIAL_PATH.parent
SYSTEMD_CREDENTIAL_BINDING = ("github_token", "/etc/ci-runner/github.token")
SYSTEMD_UNIT_OBJECT = "/org/freedesktop/systemd1/unit/ci_2drunner_2dmanager_2eservice"
OWNER = "Tuinstra-DEV"
TARGET_REPOSITORY = "Tuinstra-DEV/wodiq-platform"
RUNNER_LABEL = "trusted-heavy"
BASE_REPOSITORIES = [
    "Tuinstra-DEV/gate",
    "Tuinstra-DEV/wodiq-app",
    "Tuinstra-DEV/tracker",
    "Tuinstra-DEV/notify",
    "Tuinstra-DEV/console",
    "Tuinstra-DEV/wodiq-site",
    "Tuinstra-DEV/marcel-site",
    "Tuinstra-DEV/tuinstra-site",
]
EXPECTED_REPOSITORIES = BASE_REPOSITORIES[:2] + [TARGET_REPOSITORY] + BASE_REPOSITORIES[2:]
LIBVIRT_URI = "qemu:///system"
DOMAIN_PREFIX = "sanctuary-ci-"

Command = Callable[..., subprocess.CompletedProcess[bytes]]


class ActivationError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_regular(path: Path) -> tuple[bytes, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ActivationError(f"cannot inspect {path}: {exc.strerror}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise ActivationError(f"refusing non-regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            data = stream.read()
    except OSError as exc:
        raise ActivationError(f"cannot read {path}: {exc.strerror}") from exc
    if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
        raise ActivationError(f"file changed while being read: {path}")
    return data, opened


def _parse_config(data: bytes) -> tuple[dict, list[str], tuple[int, int]]:
    try:
        text = data.decode("utf-8")
        config = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ActivationError("manager configuration is not valid UTF-8 TOML") from exc

    required = {
        "allowed_owner", "repositories", "runner_label", "max_concurrency",
        "state_dir", "overlay_root", "github_token_file", "github_api_url",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise ActivationError("manager configuration is missing required fields")
    repositories = config["repositories"]
    if not isinstance(repositories, list) or any(not isinstance(item, str) for item in repositories):
        raise ActivationError("manager repository allowlist is malformed")
    if len(repositories) != len(set(repositories)):
        raise ActivationError("manager repository allowlist contains duplicates")
    if config["allowed_owner"] != OWNER or config["runner_label"] != RUNNER_LABEL \
            or config["max_concurrency"] != 1:
        raise ActivationError("manager owner, runner label, or concurrency is unexpected")
    if config["state_dir"] != str(STATE_DIR) or config["overlay_root"] != str(OVERLAY_ROOT):
        raise ActivationError("manager state or overlay path is unexpected")
    if config["github_token_file"] != str(SYSTEMD_CREDENTIAL_PATH):
        raise ActivationError("manager credential path differs from the reviewed systemd credential")
    if not isinstance(config["github_api_url"], str):
        raise ActivationError("manager API URL is malformed")
    if config["github_api_url"] != "https://api.github.com":
        raise ActivationError("manager API URL differs from the reviewed GitHub origin")
    if repositories not in (BASE_REPOSITORIES, EXPECTED_REPOSITORIES):
        raise ActivationError("manager repository allowlist differs from the reviewed set")

    block_matches = list(re.finditer(
        r"(?m)^repositories[ \t]*=[ \t]*\[(?P<body>.*?)^\][ \t]*$",
        text,
        re.DOTALL,
    ))
    if len(block_matches) != 1:
        raise ActivationError("manager repository array has an unsupported format")
    body = block_matches[0].group("body")
    literal_items = []
    for line in body.splitlines():
        if not line.strip():
            continue
        item = re.fullmatch(r'\s*"([^"\r\n]+)"\s*,?\s*', line)
        if item is None:
            raise ActivationError("manager repository array has an unsupported format")
        literal_items.append(item.group(1))
    if literal_items != repositories:
        raise ActivationError("manager repository array does not match parsed TOML")
    return config, repositories, (block_matches[0].start(), block_matches[0].end())


def _add_repository(data: bytes) -> bytes:
    _config, repositories, _span = _parse_config(data)
    if repositories == EXPECTED_REPOSITORIES:
        return data
    text = data.decode("utf-8")
    match = re.search(
        r"(?m)^repositories[ \t]*=[ \t]*\[(?P<body>.*?)^\][ \t]*$",
        text,
        re.DOTALL,
    )
    if match is None:
        raise ActivationError("manager repository array has an unsupported format")
    newline = "\r\n" if "\r\n" in text else "\n"
    anchor = f'  "{BASE_REPOSITORIES[1]}",'
    start, end = match.span("body")
    body = text[start:end]
    anchor_line = anchor + newline
    if body.count(anchor_line) != 1:
        raise ActivationError("manager repository insertion point is ambiguous")
    body = body.replace(
        anchor_line,
        anchor_line + f'  "{TARGET_REPOSITORY}",' + newline,
        1,
    )
    updated = text[:start] + body + text[end:]
    updated_bytes = updated.encode("utf-8")
    _config, updated_repositories, _span = _parse_config(updated_bytes)
    if updated_repositories != EXPECTED_REPOSITORIES:
        raise ActivationError("candidate manager repository allowlist is invalid")
    return updated_bytes


def _atomic_write(path: Path, data: bytes, metadata: os.stat_result) -> None:
    temp_name = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        os.fchmod(fd, stat.S_IMODE(metadata.st_mode))
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        temp_name = None
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        raise ActivationError(f"atomic configuration update failed: {exc.strerror}") from exc
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def _write_backup(path: Path, data: bytes, metadata: os.stat_result) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak.{stamp}.{os.getpid()}.{secrets.token_hex(4)}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(backup, flags, stat.S_IMODE(metadata.st_mode))
        os.fchown(fd, metadata.st_uid, metadata.st_gid)
        os.fchmod(fd, stat.S_IMODE(metadata.st_mode))
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        try:
            backup.unlink(missing_ok=True)
        except OSError:
            pass
        raise ActivationError(f"cannot create manager configuration backup: {exc.strerror}") from exc
    return backup


def _run(command: Command, argv: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    try:
        result = command(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise ActivationError(f"required host command is unavailable: {argv[0]}") from exc
    if check and result.returncode != 0:
        raise ActivationError(f"host command failed: {argv[0]} {argv[1]}")
    return result


def _manager_was_active(command: Command) -> bool:
    result = _run(command, ["systemctl", "is-active", SERVICE], check=False)
    if result.returncode != 0 or result.stdout.decode("utf-8", "replace").strip() != "active":
        raise ActivationError("runner manager must be active before admission change")
    return True


def _metadata_description(path: Path, metadata: os.stat_result) -> str:
    file_type = "regular" if stat.S_ISREG(metadata.st_mode) else "directory" if stat.S_ISDIR(metadata.st_mode) else "other"
    return (
        f"path={path} type={file_type} uid={metadata.st_uid} gid={metadata.st_gid} "
        f"mode={stat.S_IMODE(metadata.st_mode):04o}"
    )


def _systemd_credential_binding(command: Command) -> None:
    result = _run(command, [
        "busctl", "--json=short", "get-property", "org.freedesktop.systemd1",
        SYSTEMD_UNIT_OBJECT, "org.freedesktop.systemd1.Service", "LoadCredential",
    ])
    try:
        document = json.loads(result.stdout.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError("cannot verify the effective manager credential binding") from exc
    if document != {"type": "a(ss)", "data": [[*SYSTEMD_CREDENTIAL_BINDING]]}:
        raise ActivationError("effective manager credential binding differs from the reviewed root store")


def _read_systemd_credential(path: Path, *, credential_directory: Path | None = None,
                              expected_owner: tuple[int, int] = (0, 0)) -> str:
    if credential_directory is None:
        if path != SYSTEMD_CREDENTIAL_PATH:
            raise ActivationError("manager systemd credential path is not canonical")
        directories = (Path("/run"), Path("/run/credentials"), SYSTEMD_CREDENTIAL_DIRECTORY)
    else:
        # Unit tests map the fixed systemd path onto an isolated private fixture.
        if path.parent != credential_directory or path.name != SYSTEMD_CREDENTIAL_PATH.name:
            raise ActivationError("manager systemd credential path is not canonical")
        directories = (credential_directory,)

    for directory in directories:
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise ActivationError("cannot inspect systemd credential directory metadata") from exc
        protected = (
            stat.S_ISDIR(metadata.st_mode)
            and metadata.st_uid == expected_owner[0]
            and metadata.st_gid == expected_owner[1]
            and stat.S_IMODE(metadata.st_mode) & 0o022 == 0
        )
        if directory == directories[-1]:
            mode = stat.S_IMODE(metadata.st_mode)
            # systemd's unit credential directory must be private from other
            # users. Group access is acceptable only for root:root; there must
            # be no group/world write and no world read/traverse.
            protected = protected and mode & 0o007 == 0
        if not protected:
            raise ActivationError(
                "systemd credential directory violates the protected owner/mode contract ("
                f"{_metadata_description(directory, metadata)})"
            )

    try:
        before = path.lstat()
        before_mode = stat.S_IMODE(before.st_mode)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != expected_owner[0] \
                or before.st_gid != expected_owner[1] or before.st_nlink != 1 \
                or before_mode not in (0o400, 0o440, 0o444):
            raise ActivationError(
                "systemd credential file violates the root-owned read-only contract ("
                f"{_metadata_description(path, before)})"
            )
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ActivationError("systemd credential changed while being inspected")
            mode = stat.S_IMODE(metadata.st_mode)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != expected_owner[0] \
                    or metadata.st_gid != expected_owner[1] or metadata.st_nlink != 1 \
                    or mode not in (0o400, 0o440, 0o444):
                raise ActivationError(
                    "systemd credential file violates the root-owned read-only contract ("
                    f"{_metadata_description(path, metadata)})"
                )
            token_bytes = stream.read(4097)
    except ActivationError:
        raise
    except OSError as exc:
        raise ActivationError("manager systemd credential is unavailable") from exc
    if len(token_bytes) > 4096:
        raise ActivationError("manager credential is unexpectedly large")
    try:
        token = token_bytes.decode("ascii").strip()
    except UnicodeError as exc:
        raise ActivationError("manager credential is malformed") from exc
    if not token or "\n" in token or "\r" in token:
        raise ActivationError("manager credential is empty or malformed")
    return token


def _check_repository_access(config: dict, *, command: Command = subprocess.run,
                             opener=None, credential_file: Path | None = None,
                             credential_directory: Path | None = None,
                             credential_owner: tuple[int, int] = (0, 0)) -> None:
    """Check repository Actions and runner-list access without exposing credentials."""
    token_path = Path(config["github_token_file"])
    if token_path != SYSTEMD_CREDENTIAL_PATH:
        raise ActivationError("manager credential path is not the reviewed systemd credential")
    _systemd_credential_binding(command)
    token = _read_systemd_credential(
        credential_file or token_path,
        credential_directory=credential_directory,
        expected_owner=credential_owner,
    )

    if opener is None:
        opener = urllib.request.build_opener(_NoRedirect())
    base = config["github_api_url"].rstrip("/")
    endpoints = (
        f"{base}/repos/{OWNER}/wodiq-platform/actions/runs?status=queued&per_page=1",
        f"{base}/repos/{OWNER}/wodiq-platform/actions/runners",
    )
    for endpoint in endpoints:
        request = urllib.request.Request(
            endpoint,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "sanctuary-runner-admission-check",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            response = opener.open(request, timeout=15)
            try:
                status = response.status if hasattr(response, "status") else response.getcode()
            finally:
                response.close()
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ActivationError(
                "GitHub repository permission check failed; configuration unchanged"
            ) from exc
        if status != 200:
            label = "Actions read" if "actions/runs" in endpoint else "runner administration read"
            raise ActivationError(
                f"manager credential cannot access WODIQ Platform ({label}, HTTP {status}); "
                "configuration unchanged"
            )


def _assert_drained(command: Command, state_dir: Path = STATE_DIR,
                    overlay_root: Path = OVERLAY_ROOT) -> None:
    result = _run(command, ["virsh", "--connect", LIBVIRT_URI, "list", "--all", "--name"])
    domains = [line.strip() for line in result.stdout.decode("utf-8", "strict").splitlines() if line.strip()]
    if any(domain.startswith(DOMAIN_PREFIX) for domain in domains):
        raise ActivationError("a Sanctuary CI domain is active; let the job finish and retry")
    for path in (state_dir, overlay_root):
        try:
            if path.is_symlink() or not path.is_dir():
                raise ActivationError("runner state or overlay directory is unavailable")
            entries = list(path.iterdir())
        except OSError as exc:
            raise ActivationError("cannot inspect runner state or overlay directory") from exc
        if path == state_dir:
            pending = [entry for entry in entries if entry.name.startswith(("lease-", "cleanup-"))
                       and entry.suffix == ".json"]
            pending += [entry for entry in entries if entry.name.endswith(".tmp")]
            if pending:
                raise ActivationError("runner state contains an active lease or pending cleanup")
        elif entries:
            raise ActivationError("runner overlay directory is not empty")


def _acquire_lock(lock_path: Path):
    lock_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
        os.close(fd)
        raise ActivationError("activation lock is not owned by the current user")
    os.fchmod(fd, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise ActivationError("another runner admission operation is in progress") from exc
    return fd


def _active_hash(path: Path) -> tuple[bytes, os.stat_result, str]:
    data, metadata = _read_regular(path)
    digest = hashlib.sha256(data).hexdigest()
    return data, metadata, digest


def _pause_and_drain(command: Command, state_dir: Path = STATE_DIR,
                     overlay_root: Path = OVERLAY_ROOT) -> None:
    _manager_was_active(command)
    _run(command, ["systemctl", "stop", SERVICE])
    state = _run(command, ["systemctl", "is-active", SERVICE], check=False)
    if state.returncode != 3 or state.stdout.decode("utf-8", "replace").strip() != "inactive":
        try:
            _start_manager(command)
        except ActivationError as exc:
            raise ActivationError("runner manager state is unclear and restart failed") from exc
        raise ActivationError("runner manager did not reach inactive state; configuration unchanged")
    try:
        _assert_drained(command, state_dir, overlay_root)
    except ActivationError:
        try:
            _start_manager(command)
        except ActivationError as exc:
            raise ActivationError(
                "runner manager remains stopped after drain preflight failed; inspect service"
            ) from exc
        raise


def _start_manager(command: Command) -> None:
    _run(command, ["systemctl", "start", SERVICE])
    state = _run(command, ["systemctl", "is-active", SERVICE], check=False)
    if state.returncode != 0 or state.stdout.decode("utf-8", "replace").strip() != "active":
        raise ActivationError("runner manager did not become active")


def _rollback_after_failure(path: Path, backup: Path, metadata: os.stat_result,
                            command: Command) -> None:
    backup_data, _backup_metadata = _read_regular(backup)
    _atomic_write(path, backup_data, metadata)
    try:
        _start_manager(command)
    except ActivationError:
        raise ActivationError(
            "configuration was restored from backup but runner manager restart failed; "
            f"backup retained at {backup}"
        )


def activate(config_path: Path = CONFIG, *, command: Command = subprocess.run,
             lock_path: Path = LOCK_PATH, state_dir: Path = STATE_DIR,
             overlay_root: Path = OVERLAY_ROOT, opener=None,
             credential_file: Path | None = None,
             credential_directory: Path | None = None,
             credential_owner: tuple[int, int] = (0, 0)) -> str:
    lock_fd = _acquire_lock(lock_path)
    was_stopped = False
    backup = None
    metadata = None
    try:
        original, metadata, original_hash = _active_hash(config_path)
        _parsed, repositories, _span = _parse_config(original)
        if repositories == EXPECTED_REPOSITORIES:
            return "WODIQ Platform is already in the runner allowlist; no changes made"
        candidate = _add_repository(original)
        _check_repository_access(
            _parsed,
            command=command,
            opener=opener,
            credential_file=credential_file,
            credential_directory=credential_directory,
            credential_owner=credential_owner,
        )
        _pause_and_drain(command, state_dir, overlay_root)
        was_stopped = True

        current, current_metadata = _read_regular(config_path)
        if hashlib.sha256(current).hexdigest() != original_hash \
                or (current_metadata.st_dev, current_metadata.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ActivationError("manager configuration changed during preflight; retry after review")
        backup = _write_backup(config_path, original, metadata)
        try:
            _atomic_write(config_path, candidate, metadata)
            _config, repositories_after, _span = _parse_config(_read_regular(config_path)[0])
            if repositories_after != EXPECTED_REPOSITORIES:
                raise ActivationError("installed configuration failed repository validation")
            _start_manager(command)
            was_stopped = False
        except ActivationError as update_error:
            try:
                _rollback_after_failure(config_path, backup, metadata, command)
            except ActivationError as rollback_error:
                was_stopped = False
                raise ActivationError(
                    "configuration update failed and restoration did not complete; "
                    f"leave manager stopped and inspect backup {backup}"
                ) from rollback_error
            was_stopped = False
            raise update_error
        digest = hashlib.sha256(candidate).hexdigest()
        backup_digest = hashlib.sha256(original).hexdigest()
        return (
            f"WODIQ Platform admitted; backup={backup}; backup_sha256={backup_digest}; "
            f"config_sha256={digest}"
        )
    except ActivationError:
        if was_stopped:
            try:
                _start_manager(command)
            except ActivationError:
                raise ActivationError("runner manager remains stopped; inspect service before retry")
        raise
    finally:
        os.close(lock_fd)


def rollback(config_path: Path, backup_path: Path, *, command: Command = subprocess.run,
             lock_path: Path = LOCK_PATH, state_dir: Path = STATE_DIR,
             overlay_root: Path = OVERLAY_ROOT) -> str:
    lock_fd = _acquire_lock(lock_path)
    was_stopped = False
    try:
        if backup_path.parent != config_path.parent or not backup_path.name.startswith(config_path.name + ".bak."):
            raise ActivationError("rollback backup must be a recorded manager configuration backup")
        current, current_metadata, _current_hash = _active_hash(config_path)
        backup_data, backup_metadata = _read_regular(backup_path)
        current_config, current_repositories, _span = _parse_config(current)
        backup_config, backup_repositories, _span = _parse_config(backup_data)
        if current_repositories != EXPECTED_REPOSITORIES or backup_repositories != BASE_REPOSITORIES:
            raise ActivationError("current file or backup is outside the reviewed rollback state")
        current_without_repos = dict(current_config)
        backup_without_repos = dict(backup_config)
        current_without_repos.pop("repositories")
        backup_without_repos.pop("repositories")
        if current_without_repos != backup_without_repos:
            raise ActivationError("manager configuration changed beyond the repository allowlist")

        _pause_and_drain(command, state_dir, overlay_root)
        was_stopped = True
        latest, latest_metadata = _read_regular(config_path)
        if latest != current or (latest_metadata.st_dev, latest_metadata.st_ino) != (
                current_metadata.st_dev, current_metadata.st_ino):
            raise ActivationError("manager configuration changed during rollback preflight")
        # Preserve the current admitted configuration too, so rollback remains reversible.
        reverse_backup = _write_backup(config_path, current, current_metadata)
        restored, restored_metadata = backup_data, current_metadata
        _atomic_write(config_path, restored, restored_metadata)
        try:
            _parse_config(_read_regular(config_path)[0])
            _start_manager(command)
            was_stopped = False
        except ActivationError as rollback_error:
            try:
                _atomic_write(config_path, current, current_metadata)
                _start_manager(command)
                was_stopped = False
            except ActivationError as restore_error:
                was_stopped = False
                raise ActivationError(
                    "rollback failed; runner manager is left stopped and admitted "
                    "configuration backup retained at "
                    f"{reverse_backup}"
                ) from restore_error
            raise rollback_error
        reverse_digest = hashlib.sha256(current).hexdigest()
        return (
            f"WODIQ Platform admission rolled back; reverse_backup={reverse_backup}; "
            f"reverse_backup_sha256={reverse_digest}"
        )
    except ActivationError:
        if was_stopped:
            try:
                _start_manager(command)
            except ActivationError:
                raise ActivationError("runner manager remains stopped; inspect service before retry")
        raise
    finally:
        os.close(lock_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rollback", type=Path, help="restore a backup created by this helper")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("run this helper with sudo on Sanctuary", file=sys.stderr)
        return 2
    try:
        metadata = CONFIG.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0:
            raise ActivationError("manager configuration must be a root-owned regular file")
        message = rollback(CONFIG, args.rollback) if args.rollback else activate()
    except ActivationError as exc:
        print(f"runner admission unchanged or safely rolled back: {exc}", file=sys.stderr)
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
