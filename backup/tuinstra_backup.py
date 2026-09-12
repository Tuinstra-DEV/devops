#!/usr/bin/env python3
"""Restricted production backup/export and Sanctuary pull worker.

The public CLI accepts host/application identifiers only. All paths and commands
come from root-owned JSON configuration installed by Ansible.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any, BinaryIO


SCHEMA_VERSION = 1
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ARTIFACT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
POLICY_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
ROOT_UID = 0
PUBLIC_MANIFEST_RESERVE = 64 * 1024
PUBLIC_MANIFEST_KEYS = {"schema_version", "artifact_id", "host_slug", "app_id", "adapter",
                        "created_at", "payload_sha256", "payload_bytes"}
CATALOG_LIMIT = 500
CATALOG_POINT_KEYS = {"snapshot_id", "artifact_id", "host_slug", "app_id", "created_at", "stored_at",
                      "integrity_checked_at", "integrity_coverage", "payload_bytes", "payload_sha256",
                      "policy_version", "engine", "state", "removed_at", "repository_observed_at",
                      "run_id", "trigger", "source_id", "destination_id"}
TRIGGERS = {"scheduled", "console", "manual"}
ATTEMPT_STATUSES = {"running", "failed", "succeeded", "uncertain"}
ERROR_CODES = {None, "backup_failed", "capacity_exhausted", "source_unreachable", "transfer_integrity",
               "operation_busy", "policy_invalid", "interrupted"}


class BackupError(RuntimeError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_config(path: str) -> dict[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise BackupError("configuration must be a regular file")
    mode = source.stat().st_mode & 0o777
    if mode & 0o022:
        raise BackupError("configuration must not be group/world writable")
    data = json.loads(source.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise BackupError("unsupported configuration schema")
    return data


def require_id(value: str, label: str) -> str:
    if not ID_RE.fullmatch(value):
        raise BackupError(f"invalid {label}")
    return value


def require_artifact(value: str) -> str:
    if not ARTIFACT_RE.fullmatch(value):
        raise BackupError("invalid artifact id")
    return value


def reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        current /= part
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise BackupError("configured path contains a symbolic link")


def ensure_directory(path: Path, mode: int) -> None:
    reject_symlink_components(path)
    path.mkdir(parents=True, exist_ok=True)
    reject_symlink_components(path)
    if not path.is_dir():
        raise BackupError("configured directory is unavailable")
    os.chmod(path, mode)


def validate_public_manifest(value: dict[str, Any], maximum_bytes: int) -> dict[str, Any]:
    if set(value) != PUBLIC_MANIFEST_KEYS or value.get("schema_version") != SCHEMA_VERSION:
        raise BackupError("artifact manifest schema is invalid")
    require_artifact(value["artifact_id"])
    require_id(value["host_slug"], "host slug")
    require_id(value["app_id"], "application id")
    if value["adapter"] != "postgres-compose-v1" or not SHA_RE.fullmatch(value["payload_sha256"]):
        raise BackupError("artifact manifest content is invalid")
    if not isinstance(value["payload_bytes"], int) or not 0 < value["payload_bytes"] <= maximum_bytes:
        raise BackupError("artifact size is outside policy")
    if not isinstance(value["created_at"], str) or not value["created_at"].endswith("Z"):
        raise BackupError("artifact timestamp is invalid")
    return value


def ensure_private_runtime_directory(path: Path) -> None:
    reject_symlink_components(path)
    path.mkdir(parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)
        info = path.lstat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise BackupError("privileged ingest directory has unsafe ownership or permissions")


def copy_from_directory_fd(directory_fd: int, name: str, destination: Path, maximum_bytes: int) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        info = os.fstat(source_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum_bytes:
            raise BackupError("incoming artifact member is invalid")
        target_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            copied = 0
            while True:
                chunk = os.read(source_fd, min(1024 * 1024, maximum_bytes + 1 - copied))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > maximum_bytes:
                    raise BackupError("incoming artifact member exceeds policy")
                os.write(target_fd, chunk)
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)


def materialize_ingest(config: dict[str, Any], artifact_id: str) -> Path:
    incoming_root = Path(config["incoming_root"])
    ingest_root = Path(config["ingest_root"])
    reject_symlink_components(incoming_root)
    ensure_private_runtime_directory(ingest_root)
    final = ingest_root / artifact_id
    if final.exists():
        if final.is_symlink() or not final.is_dir() or final.stat().st_uid != os.geteuid():
            raise BackupError("privileged ingest artifact is not controlled")
        members = {item.name for item in final.iterdir()}
        if members != {"manifest.json", "payload.age"}:
            raise BackupError("privileged ingest artifact has unexpected members")
        for item in final.iterdir():
            info = item.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise BackupError("privileged ingest artifact has unsafe members")
        return final
    root_fd = os.open(incoming_root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
    try:
        artifact_fd = os.open(artifact_id, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                              dir_fd=root_fd)
        temporary = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=ingest_root))
        os.chmod(temporary, 0o700)
        try:
            copy_from_directory_fd(artifact_fd, "manifest.json", temporary / "manifest.json", PUBLIC_MANIFEST_RESERVE)
            copy_from_directory_fd(artifact_fd, "payload.age", temporary / "payload.age",
                                   int(config["max_artifact_bytes"]))
            os.replace(temporary, final)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        finally:
            os.close(artifact_fd)
    finally:
        os.close(root_fd)
    return final


def policy_document(host_slug: str, app_id: str, policy_version: str, hour: int, minute: int,
                    daily: int, weekly: int, monthly: int) -> dict[str, Any]:
    require_id(host_slug, "host slug")
    require_id(app_id, "application id")
    if not POLICY_RE.fullmatch(policy_version):
        raise BackupError("invalid policy version")
    bounded = (("hour", hour, 0, 23), ("minute", minute, 0, 59), ("daily", daily, 1, 31),
               ("weekly", weekly, 1, 52), ("monthly", monthly, 1, 24))
    for label, value, lower, upper in bounded:
        if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
            raise BackupError(f"invalid policy {label}")
    return {"schema_version": SCHEMA_VERSION, "policy_version": policy_version,
            "host_slug": host_slug, "app_id": app_id,
            "schedule": {"frequency": "daily", "timezone": "Europe/Amsterdam",
                         "hour": hour, "minute": minute},
            "retention": {"daily": daily, "weekly": weekly, "monthly": monthly}}


def document_hash(document: dict[str, Any]) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def policy_path(config: dict[str, Any], host_slug: str, app_id: str) -> Path:
    return Path(config["policy_root"]) / host_slug / f"{app_id}.json"


def operation_lock_path(config: dict[str, Any], host_slug: str, app_id: str) -> Path:
    require_id(host_slug, "host slug")
    require_id(app_id, "application id")
    return Path(config["operation_lock_root"]) / f"{host_slug}--{app_id}.lock"


def host_operation_lock_path(config: dict[str, Any], host_slug: str) -> Path:
    require_id(host_slug, "host slug")
    return Path(config["host_lock_root"]) / f"operations.host.{host_slug}.lock"


@contextlib.contextmanager
def host_operation_lock(config: dict[str, Any], host_slug: str):
    path = host_operation_lock_path(config, host_slug)
    reject_symlink_components(path.parent)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BackupError("authoritative host operation lock is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID
                or stat.S_IMODE(info.st_mode) != 0o660):
            raise BackupError("authoritative host operation lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("host has another active operation") from exc
        yield
    finally:
        os.close(descriptor)


def load_policy(config: dict[str, Any], host_slug: str, app_id: str) -> dict[str, Any]:
    try:
        value = json.loads(policy_path(config, host_slug, app_id).read_text(encoding="utf-8"))
    except OSError as exc:
        raise BackupError("active backup policy is unavailable") from exc
    expected_hash = value.pop("plan_hash", None)
    value.pop("applied_at", None)
    if expected_hash != document_hash(value):
        raise BackupError("stored policy hash mismatch")
    value["plan_hash"] = expected_hash
    return value


def assert_allowlisted_target(config: dict[str, Any], host_slug: str, app_id: str) -> dict[str, Any]:
    matches = [item for item in config["hosts"] if item["host_slug"] == host_slug]
    if len(matches) != 1 or app_id not in matches[0]["applications"]:
        raise BackupError("backup policy target is not allowlisted")
    return matches[0]


def reconcile_policy(config: dict[str, Any], document: dict[str, Any], plan_hash: str) -> dict[str, Any]:
    host_slug, app_id = document["host_slug"], document["app_id"]
    host = assert_allowlisted_target(config, host_slug, app_id)
    if not SHA_RE.fullmatch(plan_hash) or document_hash(document) != plan_hash:
        raise BackupError("approved policy plan hash mismatch")
    with host_operation_lock(config, host_slug), lock(operation_lock_path(config, host_slug, app_id)):
        destination = policy_path(config, host_slug, app_id)
        changed = True
        if destination.exists():
            current = json.loads(destination.read_text(encoding="utf-8"))
            if current.get("policy_version") == document["policy_version"] and current.get("plan_hash") != plan_hash:
                raise BackupError("policy version is immutable")
            changed = current.get("plan_hash") != plan_hash
        stored = {**document, "plan_hash": plan_hash, "applied_at": now()}
        # applied_at is audit metadata and intentionally excluded from the immutable plan hash.
        if changed:
            atomic_json(destination, stored, 0o644)
        unit_id = f"{host_slug}--{app_id}"
        unit_root = Path(config["systemd_unit_root"])
        service = unit_root / f"tuinstra-backup-cycle-{unit_id}.service"
        timer = unit_root / f"tuinstra-backup-cycle-{unit_id}.timer"
        fallback_service = unit_root / f"tuinstra-backup-daily-fallback-{unit_id}.service"
        fallback_timer = unit_root / f"tuinstra-backup-daily-fallback-{unit_id}.timer"
        executable = config.get("executable", "/usr/local/libexec/tuinstra-backup/tuinstra_backup.py")
        config_path = config.get("installed_config", "/etc/tuinstra-backup/worker.json")
        service_text = ("[Unit]\nDescription=Durable backup cycle for " + unit_id + "\n"
            "After=network-online.target\nWants=network-online.target\n\n[Service]\nType=oneshot\n"
            "User=tuinstra-backup\nGroup=tuinstra-backup\nUMask=0077\n"
            "SupplementaryGroups=tuinstra-ops\n"
            f"LoadCredential={host['credential_name']}:{host['identity_file']}\n"
            f"ExecStart={executable} --config {config_path} cycle --host {host_slug} --app {app_id} "
            f"--policy-version {document['policy_version']} --plan-hash {plan_hash} --trigger scheduled\n"
            "PrivateTmp=true\nProtectHome=true\nProtectSystem=strict\n"
            f"ReadWritePaths={config['work_dir']} {config['incoming_root']} {config['pull_lock_root']} "
            f"{config['ingest_root']} {config['operation_lock_root']} {config['catalog_root']} "
            f"{config['repository_root']} {config['host_lock_root']}\n")
        schedule = document["schedule"]
        fallback_total_minutes = (schedule["hour"] * 60 + schedule["minute"] + 75) % (24 * 60)
        fallback_hour, fallback_minute = divmod(fallback_total_minutes, 60)
        timer_text = ("[Unit]\nDescription=Daily durable backup schedule for " + unit_id + "\n\n[Timer]\n"
            f"OnCalendar=*-*-* {schedule['hour']:02d}:{schedule['minute']:02d}:00 Europe/Amsterdam\n"
            "Persistent=true\nRandomizedDelaySec=10m\n"
            f"Unit={service.name}\n\n[Install]\nWantedBy=timers.target\n")
        fallback_service_text = service_text.replace(
            f"ExecStart={executable} --config {config_path} cycle",
            f"ExecStart={executable} --config {config_path} ensure-daily",
        ).replace("Description=Durable backup cycle", "Description=Daily backup fallback")
        fallback_timer_text = ("[Unit]\nDescription=Daily backup fallback for " + unit_id + "\n\n[Timer]\n"
            f"OnCalendar=*-*-* {fallback_hour:02d}:{fallback_minute:02d}:00 Europe/Amsterdam\n"
            "Persistent=true\nRandomizedDelaySec=5m\n"
            f"Unit={fallback_service.name}\n\n[Install]\nWantedBy=timers.target\n")
        service_changed = atomic_text(service, service_text)
        timer_changed = atomic_text(timer, timer_text)
        fallback_service_changed = atomic_text(fallback_service, fallback_service_text)
        fallback_timer_changed = atomic_text(fallback_timer, fallback_timer_text)
        units_changed = service_changed or timer_changed or fallback_service_changed or fallback_timer_changed
        if units_changed:
            run(["systemctl", "daemon-reload"])
        for timer_name in (timer.name, fallback_timer.name):
            run(["systemctl", "enable", "--now", timer_name])
            run(["systemctl", "is-enabled", "--quiet", timer_name])
            run(["systemctl", "is-active", "--quiet", timer_name])
    return {**document, "plan_hash": plan_hash,
            "status": "applied" if changed or units_changed else "unchanged"}


def atomic_text(path: Path, value: str, mode: int = 0o644) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not path.is_symlink() and path.read_text(encoding="utf-8") == value:
        return False
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return True


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any], mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, mode)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextlib.contextmanager
def lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupError("another backup operation is active") from exc
        yield


def run(argv: list[str], *, stdin: BinaryIO | None = None, stdout: BinaryIO | None = None,
        env: dict[str, str] | None = None, timeout: int = 3600) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, stdin=stdin, stdout=stdout or subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=timeout, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BackupError(f"external command failed: {Path(argv[0]).name}") from exc


def application(config: dict[str, Any], app_id: str) -> dict[str, Any]:
    require_id(app_id, "application id")
    matches = [item for item in config.get("applications", []) if item.get("app_id") == app_id]
    if len(matches) != 1 or not matches[0].get("enabled", False):
        raise BackupError("application is not enabled")
    return matches[0]


def spool_usage(spool: Path) -> int:
    return sum(item.stat().st_size for item in spool.iterdir() if item.is_file() and not item.is_symlink())


def copy_regular(source: Path, target: Path) -> dict[str, Any]:
    if source.is_symlink() or not source.is_file():
        raise BackupError("configured backup input must be a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target, follow_symlinks=False)
    os.chmod(target, 0o600)
    return {"name": target.as_posix(), "sha256": sha256(target), "bytes": target.stat().st_size}


def compose_images(app: dict[str, Any]) -> list[str]:
    result = run(["docker", "compose", "--project-name", app["compose_project"],
                  "--file", app["compose_file"], "config", "--images"])
    images = sorted(set(result.stdout.decode().splitlines()))
    if not images or any("@sha256:" not in image for image in images):
        raise BackupError("all application images must be pinned by digest")
    return images


def create_export(config: dict[str, Any], app_id: str) -> dict[str, Any]:
    host = require_id(config["host_slug"], "host slug")
    app = application(config, app_id)
    if app.get("adapter") != "postgres-compose-v1":
        raise BackupError("unsupported application adapter")
    spool = Path(config["spool_dir"])
    receipts = Path(config["receipt_dir"])
    work_root = Path(config["work_dir"])
    recipient = Path(config["age_recipient_file"])
    if recipient.is_symlink() or not recipient.is_file():
        raise BackupError("age recipient file is unavailable")
    ensure_directory(spool, 0o750)
    ensure_directory(receipts, 0o750)
    ensure_directory(work_root, 0o700)
    artifact_id = str(uuid.uuid4())
    final_payload = spool / f"{artifact_id}.age"
    final_manifest = spool / f"{artifact_id}.json"
    quota = int(config["spool_quota_bytes"])
    with lock(Path(config["lock_file"])):
        initial_usage = spool_usage(spool)
        if initial_usage >= quota:
            raise BackupError("backup spool quota reached")
        with tempfile.TemporaryDirectory(prefix=f"{app_id}-", dir=work_root) as temporary:
            root = Path(temporary)
            payload = root / "payload"
            payload.mkdir(mode=0o700)
            database_dump = payload / "database.dump"
            compose = ["docker", "compose", "--project-name", app["compose_project"],
                       "--file", app["compose_file"]]
            dump_command = compose + ["exec", "-T", app["postgres_service"], "sh", "-eu", "-c",
                'exec pg_dump --format=custom --username="$POSTGRES_USER" "$POSTGRES_DB"']
            with database_dump.open("xb") as handle:
                run(dump_command, stdout=handle)
            os.chmod(database_dump, 0o600)
            if database_dump.stat().st_size == 0:
                raise BackupError("database export is empty")
            inputs: list[dict[str, Any]] = [{"name": "database.dump", "sha256": sha256(database_dump),
                                             "bytes": database_dump.stat().st_size}]
            for item in app["included_files"]:
                logical = require_id(item["name"], "input name")
                copied = copy_regular(Path(item["path"]), payload / "files" / logical)
                copied["name"] = f"files/{logical}"
                inputs.append(copied)
            internal = {
                "schema_version": SCHEMA_VERSION, "artifact_id": artifact_id,
                "host_slug": host, "app_id": app_id, "adapter": app["adapter"],
                "created_at": now(), "database_service": app["postgres_service"],
                "images": compose_images(app), "inputs": inputs,
            }
            atomic_json(payload / "backup-manifest.json", internal, 0o600)
            archive = root / "payload.tar"
            with tarfile.open(archive, "w") as tar:
                tar.add(payload, arcname="payload", recursive=True)
            # The encrypted staging file lives beside its final name so publication cannot
            # cross filesystems when the private plaintext workspace is on tmpfs.
            encrypted = spool / f".{artifact_id}.{uuid.uuid4().hex}.age.tmp"
            try:
                run(["age", "--encrypt", "--recipients-file", str(recipient),
                     "--output", str(encrypted), str(archive)])
                payload_size = encrypted.stat().st_size
                required_space = payload_size + PUBLIC_MANIFEST_RESERVE
                if initial_usage + required_space > quota:
                    raise BackupError("backup would exceed spool quota")
                if shutil.disk_usage(spool).free < PUBLIC_MANIFEST_RESERVE:
                    raise BackupError("backup spool filesystem is full")
                os.chmod(encrypted, 0o640)
                with encrypted.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(encrypted, final_payload)
                public = {
                    "schema_version": SCHEMA_VERSION, "artifact_id": artifact_id, "host_slug": host,
                    "app_id": app_id, "adapter": app["adapter"], "created_at": internal["created_at"],
                    "payload_sha256": sha256(final_payload), "payload_bytes": final_payload.stat().st_size,
                }
                validate_public_manifest(public, quota)
                try:
                    atomic_json(final_manifest, public)
                except Exception:
                    final_payload.unlink(missing_ok=True)
                    raise
            finally:
                encrypted.unlink(missing_ok=True)
    return public


def ready_manifests(config: dict[str, Any]) -> list[dict[str, Any]]:
    spool = Path(config["spool_dir"])
    results = []
    for source in sorted(spool.glob("*.json")):
        if source.name.endswith(".receipt.json") or source.is_symlink():
            continue
        value = validate_public_manifest(json.loads(source.read_text(encoding="utf-8")),
                                         int(config["spool_quota_bytes"]))
        artifact_id = require_artifact(value["artifact_id"])
        payload = spool / f"{artifact_id}.age"
        if payload.is_file() and not payload.is_symlink() and sha256(payload) == value["payload_sha256"]:
            results.append(value)
    return results


def dispatch(config: dict[str, Any], original: str, output: BinaryIO) -> None:
    parts = original.split()
    if parts == ["list"]:
        output.write((json.dumps(ready_manifests(config), separators=(",", ":")) + "\n").encode())
        return
    if len(parts) == 2 and parts[0] == "export":
        app_id = require_id(parts[1], "application id")
        value = create_export(config, app_id)
        output.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
        return
    if len(parts) == 2 and parts[0] == "fetch":
        artifact_id = require_artifact(parts[1])
        payload = Path(config["spool_dir"]) / f"{artifact_id}.age"
        known = {item["artifact_id"] for item in ready_manifests(config)}
        if artifact_id not in known:
            raise BackupError("artifact is not ready")
        with payload.open("rb") as handle:
            shutil.copyfileobj(handle, output)
        return
    if len(parts) == 4 and parts[0] == "ack":
        artifact_id = require_artifact(parts[1])
        if not SHA_RE.fullmatch(parts[2]) or not RECEIPT_RE.fullmatch(parts[3]):
            raise BackupError("invalid acknowledgement")
        spool = Path(config["spool_dir"])
        manifest_path = spool / f"{artifact_id}.json"
        payload_path = spool / f"{artifact_id}.age"
        if not manifest_path.is_file() or not payload_path.is_file():
            raise BackupError("artifact is not ready")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["payload_sha256"] != parts[2] or sha256(payload_path) != parts[2]:
            raise BackupError("acknowledgement checksum mismatch")
        receipt = {**manifest, "received_at": now(), "receipt_id": parts[3]}
        atomic_json(Path(config["receipt_dir"]) / f"{artifact_id}.json", receipt)
        payload_path.unlink()
        manifest_path.unlink()
        output.write((json.dumps(receipt, separators=(",", ":")) + "\n").encode())
        return
    raise BackupError("unsupported backup transport command")


def ssh_json(host: dict[str, Any], command: str) -> Any:
    identity = ssh_identity(host)
    argv = ["ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes",
            "-o", f'UserKnownHostsFile={host["known_hosts_file"]}', "-i", identity,
            f'{host["ssh_user"]}@{host["ssh_host"]}', command]
    return json.loads(run(argv).stdout)


def ssh_identity(host: dict[str, Any]) -> str:
    credentials = os.environ.get("CREDENTIALS_DIRECTORY")
    if credentials:
        candidate = Path(credentials) / host["credential_name"]
        if candidate.is_symlink() or not candidate.is_file():
            raise BackupError("systemd SSH credential is unavailable")
        return str(candidate)
    return host["identity_file"]


def restic_env(config: dict[str, Any], host: str, app: str) -> tuple[dict[str, str], Path]:
    repository = Path(config["repository_root"]) / host / app
    password = Path(config["password_root"]) / host / f"{app}.password"
    if password.is_symlink() or not password.is_file():
        raise BackupError("restic password file is unavailable")
    env = os.environ.copy()
    env.update({"RESTIC_REPOSITORY": str(repository), "RESTIC_PASSWORD_FILE": str(password)})
    return env, repository


def catalog_path(config: dict[str, Any], host_slug: str, app_id: str) -> Path:
    return Path(config["catalog_root"]) / host_slug / f"{app_id}.json"


def empty_catalog(host_slug: str, app_id: str) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "host_slug": host_slug, "app_id": app_id,
            "generated_at": now(), "latest_attempt": None, "recovery_points": []}


def validate_catalog_point(value: dict[str, Any], host_slug: str, app_id: str,
                           maximum_bytes: int) -> None:
    if set(value) != CATALOG_POINT_KEYS or value.get("host_slug") != host_slug or value.get("app_id") != app_id:
        raise BackupError("backup catalog point identity is invalid")
    require_artifact(value.get("artifact_id", ""))
    if not SHA_RE.fullmatch(value.get("snapshot_id", "")) or not SHA_RE.fullmatch(value.get("payload_sha256", "")):
        raise BackupError("backup catalog point digest is invalid")
    if not isinstance(value.get("payload_bytes"), int) or not 0 < value["payload_bytes"] <= maximum_bytes:
        raise BackupError("backup catalog point size is invalid")
    if value.get("policy_version") is None or not POLICY_RE.fullmatch(value["policy_version"]):
        raise BackupError("backup catalog point policy is invalid")
    if value.get("engine") != "tuinstra-backup-v1" or value.get("integrity_coverage") != "full-repository-data":
        raise BackupError("backup catalog point evidence is invalid")
    require_artifact(value.get("run_id", ""))
    if (value.get("trigger") not in TRIGGERS or value.get("source_id") != host_slug
            or value.get("destination_id") != "sanctuary-restic"):
        raise BackupError("backup catalog point provenance is invalid")
    for name in ("created_at", "stored_at", "integrity_checked_at", "repository_observed_at"):
        if not isinstance(value.get(name), str) or not value[name].endswith("Z"):
            raise BackupError("backup catalog point timestamp is invalid")
    if value.get("state") == "available":
        if value.get("removed_at") is not None:
            raise BackupError("available catalog point has removal evidence")
    elif value.get("state") == "removed":
        if not isinstance(value.get("removed_at"), str) or not value["removed_at"].endswith("Z"):
            raise BackupError("removed catalog point lacks removal evidence")
    else:
        raise BackupError("backup catalog point state is invalid")


def load_catalog(config: dict[str, Any], host_slug: str, app_id: str) -> dict[str, Any]:
    path = catalog_path(config, host_slug, app_id)
    if not path.exists():
        return empty_catalog(host_slug, app_id)
    if path.is_symlink() or not path.is_file():
        raise BackupError("backup catalog is unsafe")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (set(value) != {"schema_version", "host_slug", "app_id", "generated_at", "latest_attempt", "recovery_points"}
            or value["schema_version"] != SCHEMA_VERSION or value["host_slug"] != host_slug
            or value["app_id"] != app_id or not isinstance(value["recovery_points"], list)
            or len(value["recovery_points"]) > CATALOG_LIMIT):
        raise BackupError("backup catalog schema is invalid")
    attempt = value["latest_attempt"]
    if attempt is not None:
        validate_attempt(attempt)
    for point in value["recovery_points"]:
        if not isinstance(point, dict):
            raise BackupError("backup catalog point schema is invalid")
        validate_catalog_point(point, host_slug, app_id, int(config["max_artifact_bytes"]))
    return value


def write_catalog(config: dict[str, Any], catalog: dict[str, Any]) -> None:
    available = [item for item in catalog["recovery_points"] if item.get("state") == "available"]
    removed = [item for item in catalog["recovery_points"] if item.get("state") == "removed"]
    if len(available) > CATALOG_LIMIT:
        raise BackupError("available recovery points exceed catalog safety limit")
    available.sort(key=lambda item: item.get("stored_at") or "", reverse=True)
    removed.sort(key=lambda item: item.get("removed_at") or "", reverse=True)
    catalog["recovery_points"] = available + removed[:CATALOG_LIMIT - len(available)]
    catalog["generated_at"] = now()
    atomic_json(catalog_path(config, catalog["host_slug"], catalog["app_id"]), catalog, 0o644)


def record_catalog_point(config: dict[str, Any], manifest: dict[str, Any], snapshot_id: str,
                         stored_at: str, checked_at: str, run_id: str, trigger: str) -> None:
    if not SHA_RE.fullmatch(snapshot_id):
        raise BackupError("restic did not return a full snapshot id")
    host_slug, app_id = manifest["host_slug"], manifest["app_id"]
    policy = load_policy(config, host_slug, app_id)
    catalog = load_catalog(config, host_slug, app_id)
    catalog["recovery_points"] = [
        item for item in catalog["recovery_points"] if item.get("snapshot_id") != snapshot_id
    ]
    catalog["recovery_points"].append({
        "snapshot_id": snapshot_id, "artifact_id": manifest["artifact_id"],
        "host_slug": host_slug, "app_id": app_id, "created_at": manifest["created_at"],
        "stored_at": stored_at, "integrity_checked_at": checked_at,
        "integrity_coverage": "full-repository-data", "payload_bytes": manifest["payload_bytes"],
        "payload_sha256": manifest["payload_sha256"], "policy_version": policy["policy_version"],
        "engine": "tuinstra-backup-v1", "state": "available", "removed_at": None,
        "repository_observed_at": checked_at,
        "run_id": require_artifact(run_id), "trigger": require_trigger(trigger),
        "source_id": host_slug, "destination_id": "sanctuary-restic",
    })
    write_catalog(config, catalog)


def refresh_catalog(config: dict[str, Any], host_slug: str, app_id: str,
                    snapshots: list[dict[str, Any]], checked_at: str) -> None:
    observed = {item.get("id") for item in snapshots if isinstance(item, dict) and SHA_RE.fullmatch(item.get("id", ""))}
    catalog = load_catalog(config, host_slug, app_id)
    for item in catalog["recovery_points"]:
        item["repository_observed_at"] = checked_at
        if item["snapshot_id"] in observed:
            item.update({"state": "available", "removed_at": None,
                         "integrity_checked_at": checked_at,
                         "integrity_coverage": "full-repository-data"})
        elif item.get("state") == "available":
            item.update({"state": "removed", "removed_at": checked_at})
    write_catalog(config, catalog)


def require_trigger(trigger: str) -> str:
    if trigger not in TRIGGERS:
        raise BackupError("invalid backup trigger")
    return trigger


def validate_attempt(attempt: dict[str, Any]) -> None:
    keys = {"run_id", "trigger", "started_at", "finished_at", "status", "error_code"}
    if set(attempt) != keys:
        raise BackupError("backup attempt schema is invalid")
    require_artifact(attempt.get("run_id", ""))
    require_trigger(attempt.get("trigger", ""))
    if (attempt.get("status") not in ATTEMPT_STATUSES or attempt.get("error_code") not in ERROR_CODES
            or not isinstance(attempt.get("started_at"), str) or not attempt["started_at"].endswith("Z")):
        raise BackupError("backup attempt evidence is invalid")
    if attempt["status"] == "running":
        if attempt["finished_at"] is not None or attempt["error_code"] is not None:
            raise BackupError("running backup attempt has terminal evidence")
    elif (not isinstance(attempt.get("finished_at"), str) or not attempt["finished_at"].endswith("Z")
          or (attempt["status"] == "succeeded" and attempt["error_code"] is not None)
          or (attempt["status"] != "succeeded" and attempt["error_code"] is None)):
        raise BackupError("terminal backup attempt evidence is invalid")


def attempt_start(config: dict[str, Any], host_slug: str, app_id: str, trigger: str) -> dict[str, Any]:
    assert_allowlisted_target(config, host_slug, app_id)
    value = load_catalog(config, host_slug, app_id)
    attempt = {"run_id": str(uuid.uuid4()), "trigger": require_trigger(trigger), "started_at": now(),
               "finished_at": None, "status": "running", "error_code": None}
    value["latest_attempt"] = attempt
    write_catalog(config, value)
    return attempt


def attempt_finish(config: dict[str, Any], host_slug: str, app_id: str, run_id: str,
                   status: str, error_code: str | None) -> dict[str, Any]:
    require_artifact(run_id)
    if status not in {"failed", "succeeded", "uncertain"} or error_code not in ERROR_CODES:
        raise BackupError("invalid backup attempt result")
    if (status == "succeeded") != (error_code is None):
        raise BackupError("backup attempt status and error code disagree")
    value = load_catalog(config, host_slug, app_id)
    attempt = value.get("latest_attempt")
    if not isinstance(attempt, dict) or attempt.get("run_id") != run_id or attempt.get("status") != "running":
        raise BackupError("backup attempt result does not match active run")
    attempt.update({"finished_at": now(), "status": status, "error_code": error_code})
    write_catalog(config, value)
    return attempt


def catalog(config: dict[str, Any], host_slug: str, app_id: str) -> dict[str, Any]:
    assert_allowlisted_target(config, host_slug, app_id)
    value = load_catalog(config, host_slug, app_id)
    attempt = value.get("latest_attempt")
    if isinstance(attempt, dict) and attempt.get("status") == "running":
        started = dt.datetime.fromisoformat(attempt["started_at"].replace("Z", "+00:00"))
        if dt.datetime.now(dt.timezone.utc) - started > dt.timedelta(hours=4):
            value["latest_attempt"] = {**attempt, "status": "uncertain", "finished_at": now(),
                                       "error_code": "interrupted"}
    return value


def validate_durable_result(manifest: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    for key in PUBLIC_MANIFEST_KEYS:
        if stored.get(key) != manifest.get(key):
            raise BackupError("privileged ingest result identity mismatch")
    if (not SHA_RE.fullmatch(stored.get("snapshot_id", ""))
            or stored.get("integrity_coverage") != "full-repository-data"
            or not isinstance(stored.get("stored_at"), str)
            or not isinstance(stored.get("integrity_checked_at"), str)):
        raise BackupError("privileged ingest did not return durable full-integrity evidence")
    return stored


def pull_host(config: dict[str, Any], host_slug: str, run_id: str | None = None,
              trigger: str = "manual") -> list[dict[str, Any]]:
    require_id(host_slug, "host slug")
    matches = [item for item in config["hosts"] if item["host_slug"] == host_slug]
    if len(matches) != 1:
        raise BackupError("host is not allowlisted")
    host = matches[0]
    run_id = require_artifact(run_id or str(uuid.uuid4()))
    trigger = require_trigger(trigger)
    if not host["applications"]:
        return [{"status": "not-applicable", "host_slug": host_slug,
                 "reason": "no-enabled-production-applications"}]
    received = []
    Path(config["work_dir"]).mkdir(parents=True, exist_ok=True)
    incoming_root = Path(config["incoming_root"])
    incoming_root.mkdir(parents=True, exist_ok=True)
    with lock(Path(config["pull_lock_root"]) / f"{host_slug}.lock"):
        manifests = ssh_json(host, "list")
        for manifest in manifests:
            manifest = validate_public_manifest(manifest, int(config["max_artifact_bytes"]))
            artifact_id = require_artifact(manifest["artifact_id"])
            app_id = require_id(manifest["app_id"], "application id")
            if manifest["host_slug"] != host_slug or app_id not in host["applications"]:
                raise BackupError("remote artifact identity is not allowlisted")
            incoming = incoming_root / artifact_id
            if not incoming.exists():
                temporary = Path(tempfile.mkdtemp(prefix=f".{artifact_id}-", dir=incoming_root))
                payload = temporary / "payload.age"
                argv = ["ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes",
                        "-o", f'UserKnownHostsFile={host["known_hosts_file"]}', "-i", ssh_identity(host),
                        f'{host["ssh_user"]}@{host["ssh_host"]}', f"fetch {artifact_id}"]
                try:
                    with payload.open("xb") as output:
                        run(argv, stdout=output)
                    if sha256(payload) != manifest["payload_sha256"] or payload.stat().st_size != manifest["payload_bytes"]:
                        raise BackupError("received artifact failed checksum or size validation")
                    atomic_json(temporary / "manifest.json", manifest, 0o600)
                    os.replace(temporary, incoming)
                except Exception:
                    shutil.rmtree(temporary, ignore_errors=True)
                    raise
            else:
                if incoming.is_symlink() or not incoming.is_dir():
                    raise BackupError("incoming artifact is not a controlled directory")
                local_manifest = json.loads((incoming / "manifest.json").read_text(encoding="utf-8"))
                if local_manifest != manifest:
                    raise BackupError("incoming artifact identity changed during retry")
            ingest_argv = ["sudo", "-n", config["executable"], "--config", config["installed_config"],
                           "ingest", "--host", host_slug, "--app", app_id, "--artifact", artifact_id,
                           "--run-id", run_id, "--trigger", trigger]
            stored = validate_durable_result(manifest, json.loads(run(ingest_argv).stdout))
            snapshot_id = stored["snapshot_id"]
            receipt_id = f"restic:{snapshot_id}"
            ssh_json(host, f'ack {artifact_id} {manifest["payload_sha256"]} {receipt_id}')
            cleanup_argv = ["sudo", "-n", config["executable"], "--config", config["installed_config"],
                            "ingest-cleanup", "--artifact", artifact_id]
            run(cleanup_argv)
            shutil.rmtree(incoming)
            received.append(stored)
    return received


def ingest_artifact(config: dict[str, Any], host_slug: str, app_id: str, artifact_id: str,
                    run_id: str, trigger: str) -> dict[str, Any]:
    require_artifact(artifact_id)
    assert_allowlisted_target(config, host_slug, app_id)
    with lock(operation_lock_path(config, host_slug, app_id)):
        incoming = materialize_ingest(config, artifact_id)
        manifest_path, payload = incoming / "manifest.json", incoming / "payload.age"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = validate_public_manifest(manifest, int(config["max_artifact_bytes"]))
        if (manifest.get("artifact_id") != artifact_id or manifest.get("host_slug") != host_slug
                or manifest.get("app_id") != app_id):
            raise BackupError("incoming artifact identity mismatch")
        if (sha256(payload) != manifest.get("payload_sha256")
                or payload.stat().st_size != manifest.get("payload_bytes")):
            raise BackupError("incoming artifact failed checksum or size validation")
        env, repository = restic_env(config, host_slug, app_id)
        repository.mkdir(parents=True, exist_ok=True)
        if not (repository / "config").exists():
            run(["restic", "init"], env=env)
        known = json.loads(run(["restic", "snapshots", "--json", "--tag", f"artifact:{artifact_id}"], env=env).stdout)
        if known:
            snapshot_id = known[-1]["id"]
        else:
            result = run(["restic", "backup", "--json", "--tag", f"host:{host_slug}",
                          "--tag", f"app:{app_id}", "--tag", f"artifact:{artifact_id}", str(incoming)], env=env)
            messages = [json.loads(line) for line in result.stdout.decode().splitlines() if line.startswith("{")]
            summaries = [item for item in messages if item.get("message_type") == "summary"]
            if not summaries or not summaries[-1].get("snapshot_id"):
                raise BackupError("restic did not return a durable snapshot id")
            snapshot_id = summaries[-1]["snapshot_id"]
        run(["restic", "check", "--read-data"], env=env)
        stored_at, checked_at = now(), now()
        record_catalog_point(config, manifest, snapshot_id, stored_at, checked_at, run_id, trigger)
        return {**manifest, "snapshot_id": snapshot_id, "stored_at": stored_at, "integrity_checked_at": checked_at,
                "integrity_coverage": "full-repository-data"}


def cleanup_ingest(config: dict[str, Any], artifact_id: str) -> None:
    require_artifact(artifact_id)
    ingest_root = Path(config["ingest_root"])
    ensure_private_runtime_directory(ingest_root)
    target = ingest_root / artifact_id
    if target.exists():
        if target.is_symlink() or not target.is_dir() or target.stat().st_uid != os.geteuid():
            raise BackupError("privileged ingest cleanup target is not controlled")
        shutil.rmtree(target)


def check_repository(config: dict[str, Any], host_slug: str, app_id: str) -> dict[str, Any]:
    require_id(host_slug, "host slug")
    require_id(app_id, "application id")
    with host_operation_lock(config, host_slug), lock(operation_lock_path(config, host_slug, app_id)):
        env, repository = restic_env(config, host_slug, app_id)
        if not (repository / "config").is_file():
            raise BackupError("backup repository is not initialized")
        run(["restic", "check", "--read-data"], env=env)
        snapshots = json.loads(run(["restic", "snapshots", "--json"], env=env).stdout)
        checked_at = now()
        refresh_catalog(config, host_slug, app_id, snapshots, checked_at)
    return {"status": "passed", "host_slug": host_slug, "app_id": app_id,
            "integrity_checked_at": checked_at, "integrity_coverage": "full-repository-data"}


def retain(config: dict[str, Any], host_slug: str, app_id: str, policy_version: str) -> dict[str, Any]:
    require_id(host_slug, "host slug")
    require_id(app_id, "application id")
    policy = load_policy(config, host_slug, app_id)
    if policy_version != policy["policy_version"]:
        raise BackupError("unsupported retention policy version")
    env, _ = restic_env(config, host_slug, app_id)
    with (host_operation_lock(config, host_slug), lock(operation_lock_path(config, host_slug, app_id)),
          lock(Path(config["retention_lock_file"]))):
        retention = policy["retention"]
        base = ["restic", "forget", "--group-by", "", "--keep-tag", "tuinstra:production-restore-safety",
                "--keep-daily", str(retention["daily"]),
                "--keep-weekly", str(retention["weekly"]), "--keep-monthly", str(retention["monthly"])]
        dry_run = run(base + ["--dry-run", "--json"], env=env)
        try:
            planned = json.loads(dry_run.stdout)
        except json.JSONDecodeError as exc:
            raise BackupError("retention dry-run did not return valid evidence") from exc
        groups = planned if isinstance(planned, list) else [planned]
        if not any(isinstance(group, dict) and group.get("keep") for group in groups):
            raise BackupError("retention plan would not preserve a last known snapshot")
        audit = {"schema_version": SCHEMA_VERSION, "host_slug": host_slug, "app_id": app_id,
                 "policy_version": policy_version, "plan_hash": policy["plan_hash"], "planned_at": now(),
                 "dry_run_sha256": hashlib.sha256(dry_run.stdout).hexdigest()}
        atomic_json(Path(config["retention_audit_root"]) / host_slug / app_id / f"{uuid.uuid4()}.json", audit, 0o600)
        run(base + ["--prune"], env=env)
        run(["restic", "check", "--read-data"], env=env)
        snapshots = json.loads(run(["restic", "snapshots", "--json"], env=env).stdout)
        checked_at = now()
        refresh_catalog(config, host_slug, app_id, snapshots, checked_at)
    return {**audit, "status": "retention-complete", "integrity_checked_at": checked_at,
            "integrity_coverage": "full-repository-data"}


def retain_active(config: dict[str, Any], host_slug: str, app_id: str) -> dict[str, Any]:
    policy = load_policy(config, host_slug, app_id)
    return retain(config, host_slug, app_id, policy["policy_version"])


def cycle(config: dict[str, Any], host_slug: str, app_id: str, policy_version: str,
          plan_hash: str, trigger: str, *, host_lock_held: bool = False) -> dict[str, Any]:
    host = assert_allowlisted_target(config, host_slug, app_id)
    lock_context = contextlib.nullcontext() if host_lock_held else host_operation_lock(config, host_slug)
    with lock_context:
        trigger = require_trigger(trigger)
        start_argv = ["sudo", "-n", config["executable"], "--config", config["installed_config"],
                      "attempt-start", "--host", host_slug, "--app", app_id, "--trigger", trigger]
        attempt = json.loads(run(start_argv).stdout)
        validate_attempt(attempt)
        run_id = attempt["run_id"]
        try:
            policy = load_policy(config, host_slug, app_id)
            if policy["policy_version"] != policy_version or policy["plan_hash"] != plan_hash:
                raise BackupError("active policy does not match approved backup cycle")
            exported = ssh_json(host, f"export {app_id}")
            stored = pull_host(config, host_slug, run_id, trigger)
            matches = [item for item in stored if item.get("artifact_id") == exported.get("artifact_id")]
            if len(matches) != 1:
                raise BackupError("export did not reach durable checked storage")
            result = matches[0]
        except Exception as exc:
            finish_attempt_via_sudo(config, host_slug, app_id, run_id, "failed", failure_code(exc))
            raise
        finish_attempt_via_sudo(config, host_slug, app_id, run_id, "succeeded", None)
        return {**result, "run_id": run_id, "trigger": trigger}


def failure_code(exc: Exception) -> str:
    message = str(exc).lower()
    if "quota" in message or "filesystem is full" in message or "outside policy" in message:
        return "capacity_exhausted"
    if "external command failed: ssh" in message or "offline" in message or "unreachable" in message:
        return "source_unreachable"
    if "checksum" in message or "integrity" in message:
        return "transfer_integrity"
    if "active operation" in message or "operation lock" in message:
        return "operation_busy"
    if "policy" in message:
        return "policy_invalid"
    return "backup_failed"


def finish_attempt_via_sudo(config: dict[str, Any], host_slug: str, app_id: str, run_id: str,
                            status: str, error_code: str | None) -> None:
    argv = ["sudo", "-n", config["executable"], "--config", config["installed_config"],
            "attempt-finish", "--host", host_slug, "--app", app_id, "--run-id", run_id,
            "--status", status]
    if error_code is not None:
        argv.extend(["--error-code", error_code])
    run(argv)


def ensure_daily(config: dict[str, Any], host_slug: str, app_id: str, policy_version: str,
                 plan_hash: str, trigger: str) -> dict[str, Any]:
    with host_operation_lock(config, host_slug):
        current_time = dt.datetime.now(dt.timezone.utc)
        current = catalog(config, host_slug, app_id)
        for point in current["recovery_points"]:
            if point.get("state") != "available":
                continue
            try:
                stored = dt.datetime.fromisoformat(point["stored_at"].replace("Z", "+00:00"))
            except (KeyError, TypeError, ValueError):
                raise BackupError("catalog contains an invalid storage timestamp") from None
            if dt.timedelta(0) <= current_time - stored <= dt.timedelta(hours=12):
                return {"status": "already-durable-today", "host_slug": host_slug, "app_id": app_id,
                        "snapshot_id": point["snapshot_id"], "stored_at": point["stored_at"]}
        return cycle(config, host_slug, app_id, policy_version, plan_hash, trigger, host_lock_held=True)


def safe_extract(archive: Path, target: Path) -> None:
    with tarfile.open(archive, "r") as tar:
        for member in tar.getmembers():
            relative = Path(member.name)
            if relative.is_absolute() or ".." in relative.parts or member.issym() or member.islnk() or member.isdev():
                raise BackupError("unsafe archive member")
        tar.extractall(target, filter="data")


def validate_payload(root: Path, expected: dict[str, Any]) -> dict[str, Any]:
    manifest_path = root / "payload" / "backup-manifest.json"
    internal = json.loads(manifest_path.read_text(encoding="utf-8"))
    for key in ("schema_version", "artifact_id", "host_slug", "app_id", "adapter"):
        if internal.get(key) != expected.get(key):
            raise BackupError("encrypted payload identity mismatch")
    for item in internal["inputs"]:
        source = root / "payload" / item["name"]
        if not source.is_file() or source.is_symlink() or sha256(source) != item["sha256"]:
            raise BackupError("encrypted payload input checksum mismatch")
    return internal


def restore_test(config: dict[str, Any], host_slug: str, app_id: str, snapshot: str) -> dict[str, Any]:
    if snapshot != "latest" and not re.fullmatch(r"^[0-9a-f]{8,64}$", snapshot):
        raise BackupError("invalid snapshot id")
    env, _ = restic_env(config, host_slug, app_id)
    restore_work = Path(config["restore_work_dir"])
    restore_work.mkdir(parents=True, exist_ok=True)
    with (host_operation_lock(config, host_slug), lock(operation_lock_path(config, host_slug, app_id)),
          lock(Path(config["restore_lock_file"]))):
        with tempfile.TemporaryDirectory(prefix="restore-", dir=restore_work) as temporary:
            root = Path(temporary)
            restored = root / "restic"
            run(["restic", "restore", snapshot, "--target", str(restored)], env=env)
            manifests = list(restored.rglob("manifest.json"))
            payloads = list(restored.rglob("payload.age"))
            if len(manifests) != 1 or len(payloads) != 1:
                raise BackupError("snapshot does not contain exactly one artifact")
            public = json.loads(manifests[0].read_text(encoding="utf-8"))
            if public["host_slug"] != host_slug or public["app_id"] != app_id:
                raise BackupError("snapshot identity mismatch")
            if sha256(payloads[0]) != public["payload_sha256"]:
                raise BackupError("snapshot payload checksum mismatch")
            archive = root / "payload.tar"
            run(["age", "--decrypt", "--identity", config["age_identity_file"],
                 "--output", str(archive), str(payloads[0])])
            extracted = root / "extracted"
            extracted.mkdir(mode=0o700)
            safe_extract(archive, extracted)
            internal = validate_payload(extracted, public)
            if internal["adapter"] == "postgres-compose-v1":
                run([config["restore_adapter"], str(extracted / "payload")], timeout=7200)
            else:
                raise BackupError("unsupported restore adapter")
            evidence = {**public, "snapshot_id": snapshot, "restore_tested_at": now(),
                        "restore_status": "passed", "side_effects": "blocked-loopback-only-network-namespace"}
            evidence_path = Path(config["evidence_root"]) / host_slug / app_id / f'{public["artifact_id"]}.json'
            atomic_json(evidence_path, evidence)
            return evidence


def inspect(config: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "generated_at": now(),
        "hosts": []}
    for host in config["hosts"]:
        host_item = {"host_slug": host["host_slug"], "applications": []}
        if not host["applications"]:
            host_item.update({"status": "not-applicable", "reason": "no-enabled-production-applications"})
        for app_id in host["applications"]:
            env, repository = restic_env(config, host["host_slug"], app_id)
            snapshots: list[dict[str, Any]] = []
            if (repository / "config").exists():
                snapshots = json.loads(run(["restic", "snapshots", "--json"], env=env).stdout)
            policy = load_policy(config, host["host_slug"], app_id)
            host_item["applications"].append({"app_id": app_id, "snapshot_count": len(snapshots),
                "latest_snapshot_at": max((item["time"] for item in snapshots), default=None),
                "policy_version": policy["policy_version"], "plan_hash": policy["plan_hash"],
                "schedule": policy["schedule"], "retention": policy["retention"]})
        result["hosts"].append(host_item)
    return result


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser()
    cli.add_argument("--config", required=True)
    commands = cli.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--app", required=True)
    commands.add_parser("export-all")
    dispatch_parser = commands.add_parser("dispatch")
    dispatch_parser.add_argument("--original-command")
    pull = commands.add_parser("pull")
    pull.add_argument("--host", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--host", required=True)
    ingest.add_argument("--app", required=True)
    ingest.add_argument("--artifact", required=True)
    ingest.add_argument("--run-id", required=True)
    ingest.add_argument("--trigger", choices=sorted(TRIGGERS), required=True)
    ingest_cleanup = commands.add_parser("ingest-cleanup")
    ingest_cleanup.add_argument("--artifact", required=True)
    retention = commands.add_parser("retain")
    retention.add_argument("--host", required=True)
    retention.add_argument("--app", required=True)
    retention.add_argument("--policy-version", required=True)
    retention_active = commands.add_parser("retain-active")
    retention_active.add_argument("--host", required=True)
    retention_active.add_argument("--app", required=True)
    cycle_parser = commands.add_parser("cycle")
    cycle_parser.add_argument("--host", required=True)
    cycle_parser.add_argument("--app", required=True)
    cycle_parser.add_argument("--policy-version", required=True)
    cycle_parser.add_argument("--plan-hash", required=True)
    cycle_parser.add_argument("--trigger", choices=sorted(TRIGGERS), required=True)
    ensure_daily_parser = commands.add_parser("ensure-daily")
    ensure_daily_parser.add_argument("--host", required=True)
    ensure_daily_parser.add_argument("--app", required=True)
    ensure_daily_parser.add_argument("--policy-version", required=True)
    ensure_daily_parser.add_argument("--plan-hash", required=True)
    ensure_daily_parser.add_argument("--trigger", choices=sorted(TRIGGERS), required=True)
    attempt_start_parser = commands.add_parser("attempt-start")
    attempt_start_parser.add_argument("--host", required=True)
    attempt_start_parser.add_argument("--app", required=True)
    attempt_start_parser.add_argument("--trigger", choices=sorted(TRIGGERS), required=True)
    attempt_finish_parser = commands.add_parser("attempt-finish")
    attempt_finish_parser.add_argument("--host", required=True)
    attempt_finish_parser.add_argument("--app", required=True)
    attempt_finish_parser.add_argument("--run-id", required=True)
    attempt_finish_parser.add_argument("--status", choices=["failed", "succeeded", "uncertain"], required=True)
    attempt_finish_parser.add_argument("--error-code", choices=sorted(code for code in ERROR_CODES if code))
    policy = commands.add_parser("policy-reconcile")
    policy.add_argument("--host", required=True)
    policy.add_argument("--app", required=True)
    policy.add_argument("--policy-version", required=True)
    policy.add_argument("--plan-hash", required=True)
    policy.add_argument("--hour", type=int, required=True)
    policy.add_argument("--minute", type=int, required=True)
    policy.add_argument("--daily", type=int, required=True)
    policy.add_argument("--weekly", type=int, required=True)
    policy.add_argument("--monthly", type=int, required=True)
    check = commands.add_parser("check")
    check.add_argument("--host", required=True)
    check.add_argument("--app", required=True)
    restore = commands.add_parser("restore-test")
    restore.add_argument("--host", required=True)
    restore.add_argument("--app", required=True)
    restore.add_argument("--snapshot", default="latest")
    catalog_parser = commands.add_parser("catalog")
    catalog_parser.add_argument("--host", required=True)
    catalog_parser.add_argument("--app", required=True)
    commands.add_parser("inspect")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "export":
            result: Any = create_export(config, args.app)
        elif args.command == "export-all":
            enabled = [item["app_id"] for item in config.get("applications", []) if item.get("enabled", False)]
            result = ([create_export(config, app_id) for app_id in enabled] if enabled else
                      {"status": "not-applicable", "host_slug": config["host_slug"],
                       "reason": "no-enabled-production-applications"})
        elif args.command == "dispatch":
            dispatch(config, args.original_command if args.original_command is not None else os.environ.get("SSH_ORIGINAL_COMMAND", ""), sys.stdout.buffer)
            return 0
        elif args.command == "pull":
            with host_operation_lock(config, args.host):
                result = pull_host(config, args.host)
        elif args.command == "ingest":
            if os.geteuid() != 0:
                raise BackupError("repository ingestion requires the privileged ingest service")
            result = ingest_artifact(config, args.host, args.app, args.artifact, args.run_id, args.trigger)
        elif args.command == "ingest-cleanup":
            if os.geteuid() != 0:
                raise BackupError("ingest cleanup requires the privileged ingest service")
            cleanup_ingest(config, args.artifact)
            result = {"status": "cleaned", "artifact_id": args.artifact}
        elif args.command == "retain":
            if os.geteuid() != 0:
                raise BackupError("retention requires the separate privileged service")
            result = retain(config, args.host, args.app, args.policy_version)
        elif args.command == "retain-active":
            if os.geteuid() != 0:
                raise BackupError("retention requires the separate privileged service")
            result = retain_active(config, args.host, args.app)
        elif args.command == "cycle":
            result = cycle(config, args.host, args.app, args.policy_version, args.plan_hash, args.trigger)
        elif args.command == "ensure-daily":
            result = ensure_daily(config, args.host, args.app, args.policy_version, args.plan_hash, args.trigger)
        elif args.command == "attempt-start":
            if os.geteuid() != 0:
                raise BackupError("attempt evidence requires the privileged catalog service")
            result = attempt_start(config, args.host, args.app, args.trigger)
        elif args.command == "attempt-finish":
            if os.geteuid() != 0:
                raise BackupError("attempt evidence requires the privileged catalog service")
            result = attempt_finish(config, args.host, args.app, args.run_id, args.status, args.error_code)
        elif args.command == "policy-reconcile":
            if os.geteuid() != 0:
                raise BackupError("policy reconciliation requires the privileged policy service")
            document = policy_document(args.host, args.app, args.policy_version, args.hour, args.minute,
                                       args.daily, args.weekly, args.monthly)
            result = reconcile_policy(config, document, args.plan_hash)
        elif args.command == "check":
            if os.geteuid() != 0:
                raise BackupError("integrity check requires the privileged repository service")
            result = check_repository(config, args.host, args.app)
        elif args.command == "restore-test":
            if os.geteuid() != 0:
                raise BackupError("restore test requires the privileged restore service")
            result = restore_test(config, args.host, args.app, args.snapshot)
        elif args.command == "catalog":
            result = catalog(config, args.host, args.app)
        else:
            if os.geteuid() != 0:
                raise BackupError("inspection requires the privileged repository service")
            result = inspect(config)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (BackupError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"backup operation failed: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("backup operation failed: required file or device unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
