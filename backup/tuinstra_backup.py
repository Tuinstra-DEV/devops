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
import math
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import quote


SCHEMA_VERSION = 1
ENGINE_VERSION = "tuinstra-backup-v1"
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
               "source_export_failed", "operation_busy", "policy_invalid", "interrupted"}
EXPORT_STAGE_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
EXPORT_FAILURE_RE = re.compile(r"source export failed \[stage=([a-z][a-z0-9-]{1,31})\]")
RESTORE_MIN_FREE_BYTES = 512 * 1024 * 1024
RESTORE_SPACE_MULTIPLIER = 4
RESTORE_MAX_DURATION_SECONDS = 4 * 60 * 60
RESTORE_ADAPTER_KEYS = {
    "schema_version", "adapter", "application", "status", "public_table_count",
    "application_health", "encrypted_secret_validation", "database_content_marker", "network",
    "external_effects_blocked", "host_ports",
    "postgres_image", "application_image", "containers_removed", "workspace_removed",
}
TRACKER_RESTORE_ADAPTER_KEYS = RESTORE_ADAPTER_KEYS | {"object_store_reconciliation"}
INTERNAL_MANIFEST_BASE_KEYS = {
    "schema_version", "artifact_id", "host_slug", "app_id", "adapter", "created_at",
    "database_service", "images", "inputs",
}
INTERNAL_MANIFEST_VERSION_KEYS = {"image_services", "database"}
INTERNAL_MANIFEST_TRACKER_KEYS = INTERNAL_MANIFEST_VERSION_KEYS | {"object_store_bucket"}
INPUT_KEYS = {"name", "sha256", "bytes"}
SAFETY_TAG = "tuinstra:production-restore-safety"
RECOVERY_BUNDLE = Path("/home/mtuinstra/.local/share/tuinstra-backup-recovery.json")
AGE_SECRET_PREFIX = "AGE-" + "SECRET-KEY-1"
SSH_PRIVATE_BEGIN = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
SSH_PRIVATE_END = "-----END OPENSSH " + "PRIVATE KEY-----"
UMAMI_CONTENT_MARKER_ALGORITHM = "umami-admin-two-factor-v1"
TRACKER_ADAPTER = "tracker-compose-v1"
TRACKER_MIGRATION_MARKER_ALGORITHM = "tracker-doctrine-migrations-v1"
TRACKER_OBJECT_MANIFEST_ALGORITHM = "tracker-s3-object-v1"
TRACKER_MARKER_TABLES = ("organization", "project", "story", "attachment")
TRACKER_CONTENT_MARKER_SQL = (
    "SELECT jsonb_build_object(" + ",".join(
        f"'{table}_count',(SELECT count(*) FROM {table})" for table in TRACKER_MARKER_TABLES
    ) + ")::text"
)
UMAMI_CONTENT_MARKER_SQL = (
    "SELECT jsonb_build_object("
    "'user_count',(SELECT count(*) FROM \"user\"),"
    "'two_factor_count',(SELECT count(*) FROM two_factor_auth),"
    "'admin',(SELECT jsonb_build_object('user_id',u.user_id::text,'username',u.username) "
    "FROM \"user\" AS u WHERE u.username='admin'),"
    "'admin_two_factor',(SELECT jsonb_build_object('user_id',t.user_id::text,'is_enabled',t.is_enabled,"
    "'secret',t.secret) FROM two_factor_auth AS t JOIN \"user\" AS u ON u.user_id=t.user_id "
    "WHERE u.username='admin' AND t.is_enabled IS TRUE))::text"
)


class BackupError(RuntimeError):
    pass


@contextlib.contextmanager
def export_stage(stage: str):
    """Attach a bounded, non-sensitive phase code to source export failures."""
    if not EXPORT_STAGE_RE.fullmatch(stage):
        raise BackupError("invalid source export stage")
    try:
        yield
    except BackupError as exc:
        if EXPORT_FAILURE_RE.search(str(exc)):
            raise
        raise BackupError(f"source export failed [stage={stage}]") from exc
    except Exception as exc:
        raise BackupError(f"source export failed [stage={stage}]") from exc


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
    info = path.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode:
        raise BackupError("configured directory has unsafe ownership or permissions")


def validate_public_manifest(value: dict[str, Any], maximum_bytes: int) -> dict[str, Any]:
    if set(value) != PUBLIC_MANIFEST_KEYS or value.get("schema_version") != SCHEMA_VERSION:
        raise BackupError("artifact manifest schema is invalid")
    require_artifact(value["artifact_id"])
    require_id(value["host_slug"], "host slug")
    require_id(value["app_id"], "application id")
    if value["adapter"] not in {"postgres-compose-v1", TRACKER_ADAPTER} \
            or not SHA_RE.fullmatch(value["payload_sha256"]):
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
        if (final.is_symlink() or not final.is_dir() or final.stat().st_uid != os.geteuid()
                or stat.S_IMODE(final.stat().st_mode) != 0o700):
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
            fsync_directory(ingest_root)
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


def validate_host_lock_descriptor(config: dict[str, Any], host_slug: str, descriptor: int) -> None:
    path = host_operation_lock_path(config, host_slug)
    reject_symlink_components(path.parent)
    try:
        path_info = path.stat(follow_symlinks=False)
        descriptor_info = os.fstat(descriptor)
    except OSError as exc:
        raise BackupError("inherited host operation lock is unavailable") from exc
    expected = (path_info.st_dev, path_info.st_ino)
    actual = (descriptor_info.st_dev, descriptor_info.st_ino)
    if (expected != actual or not stat.S_ISREG(path_info.st_mode)
            or path_info.st_uid != ROOT_UID or stat.S_IMODE(path_info.st_mode) != 0o660
            or not stat.S_ISREG(descriptor_info.st_mode)
            or descriptor_info.st_uid != ROOT_UID or stat.S_IMODE(descriptor_info.st_mode) != 0o660):
        raise BackupError("inherited host operation lock does not match the authoritative lock")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        raise BackupError("inherited host operation lock is not held by this operation") from exc


@contextlib.contextmanager
def host_operation_lock(config: dict[str, Any], host_slug: str, inherited_descriptor: int | None = None):
    if inherited_descriptor is not None:
        validate_host_lock_descriptor(config, host_slug, inherited_descriptor)
        yield
        return
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
            "User=root\nGroup=root\nUMask=0077\n"
            f"LoadCredential={host['credential_name']}:{host['identity_file']}\n"
            f"ExecStart={executable} --config {config_path} cycle --host {host_slug} --app {app_id} "
            f"--policy-version {document['policy_version']} --plan-hash {plan_hash} --trigger scheduled\n"
            "PrivateTmp=true\nNoNewPrivileges=true\nProtectHome=true\nProtectSystem=strict\n"
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
    fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
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
    except subprocess.CalledProcessError as exc:
        if Path(argv[0]).name == "ssh":
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else ""
            match = EXPORT_FAILURE_RE.search(stderr)
            if match:
                raise BackupError(f"source export failed [stage={match.group(1)}]") from exc
            failure = "source unreachable" if exc.returncode == 255 else "source export failed"
            raise BackupError(failure) from exc
        raise BackupError(f"external command failed: {Path(argv[0]).name}") from exc
    except subprocess.TimeoutExpired as exc:
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
    reject_symlink_components(source)
    reject_symlink_components(target.parent)
    if source.is_symlink() or not source.is_file():
        raise BackupError("configured backup input must be a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target, follow_symlinks=False)
    os.chmod(target, 0o600)
    return {"name": target.as_posix(), "sha256": sha256(target), "bytes": target.stat().st_size}


def snapshot_directory(source: Path, target: Path, maximum_bytes: int) -> None:
    reject_symlink_components(source)
    reject_symlink_components(target.parent)
    if source.is_symlink() or not source.is_dir() or maximum_bytes < 1:
        raise BackupError("configured directory input is unavailable")
    target.parent.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    try:
        with tarfile.open(target, "w") as archive:
            for current in sorted(source.rglob("*")):
                info = current.lstat()
                relative = current.relative_to(source).as_posix()
                if info.st_mode & stat.S_ISVTX:
                    raise BackupError("attachment snapshot contains unsafe permissions")
                if stat.S_ISLNK(info.st_mode):
                    raise BackupError("attachment snapshot contains a symbolic link")
                if stat.S_ISDIR(info.st_mode):
                    archive.add(current, arcname=PurePosixPath("attachments") / relative, recursive=False)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise BackupError("attachment snapshot contains an unsupported file type")
                total_bytes += info.st_size
                if total_bytes > maximum_bytes:
                    raise BackupError("attachment snapshot exceeds its size quota")
                archive.add(current, arcname=PurePosixPath("attachments") / relative, recursive=False)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    os.chmod(target, 0o600)


def _tracker_object_key(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value
            or any(character in value for character in "\x00\r\n\t")):
        raise BackupError("Tracker object key is invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or str(relative) != value or any(part in {"", ".", ".."} for part in relative.parts):
        raise BackupError("Tracker object key is invalid")
    return value


def _tracker_object_manifest(path: Path, expected_bucket: str | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("Tracker object manifest is invalid") from exc
    if not isinstance(value, dict) or set(value) != {
            "algorithm", "bucket", "objects", "object_count", "total_bytes"}:
        raise BackupError("Tracker object manifest is invalid")
    bucket = value.get("bucket")
    if (not isinstance(bucket, str) or not ID_RE.fullmatch(bucket)
            or (expected_bucket is not None and bucket != expected_bucket)):
        raise BackupError("Tracker object manifest bucket is invalid")
    objects = value.get("objects")
    if (value.get("algorithm") != TRACKER_OBJECT_MANIFEST_ALGORITHM
            or not isinstance(objects, list) or len(objects) > 4096
            or value.get("object_count") != len(objects)
            or not isinstance(value.get("total_bytes"), int)
            or isinstance(value["total_bytes"], bool) or value["total_bytes"] < 0):
        raise BackupError("Tracker object manifest is invalid")
    seen: set[str] = set()
    total = 0
    for item in objects:
        if (not isinstance(item, dict) or set(item) != {"key", "bytes", "sha256"}
                or not isinstance(item.get("bytes"), int) or isinstance(item["bytes"], bool)
                or item["bytes"] < 0 or not isinstance(item.get("sha256"), str)
                or not SHA_RE.fullmatch(item["sha256"])):
            raise BackupError("Tracker object manifest entry is invalid")
        key = _tracker_object_key(item["key"])
        if key in seen:
            raise BackupError("Tracker object manifest contains duplicate keys")
        seen.add(key)
        total += item["bytes"]
        if total > 10 * 1024 * 1024 * 1024:
            raise BackupError("Tracker object manifest exceeds its size quota")
    if total != value["total_bytes"]:
        raise BackupError("Tracker object manifest byte count is invalid")
    return value


def _tracker_object_store_bucket(app: dict[str, Any]) -> str:
    configured_bucket = app.get("object_store_bucket")
    env_name = app.get("compose_env_file")
    if (not isinstance(configured_bucket, str) or not ID_RE.fullmatch(configured_bucket)
            or not isinstance(env_name, str) or not env_name):
        raise BackupError("Tracker object-store bucket is invalid")
    env_path = Path(env_name)
    reject_symlink_components(env_path)
    try:
        env_info = env_path.lstat()
    except OSError as exc:
        raise BackupError("Tracker Compose environment is unavailable") from exc
    if (not stat.S_ISREG(env_info.st_mode) or env_path.is_symlink()
            or env_info.st_uid != ROOT_UID or stat.S_IMODE(env_info.st_mode) != 0o600):
        raise BackupError("Tracker Compose environment is unavailable")
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BackupError("Tracker Compose environment is unavailable") from exc
    matches = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator == "=" and key == "S3_BUCKET":
            matches.append(value.strip())
    if len(matches) != 1 or matches[0] != configured_bucket:
        raise BackupError("Tracker Compose environment bucket is invalid")
    return configured_bucket


def tracker_object_manifest(app: dict[str, Any], payload: Path,
                            expected_attachment_count: int = 0) -> dict[str, Any]:
    configured_bucket = _tracker_object_store_bucket(app)
    approved = app.get("approved_images")
    client_image = approved.get("minio-init") if isinstance(approved, dict) else None
    if (not isinstance(client_image, str) or not SHA_RE.fullmatch(client_image.rpartition("@sha256:")[2])):
        raise BackupError("Tracker object-store client image is invalid")
    compose = compose_command(app)
    minio_id = run([*compose, "ps", "--quiet", "minio"]).stdout.decode().strip()
    if not re.fullmatch(r"^[0-9a-f]{64}$", minio_id):
        raise BackupError("Tracker object-store service is not running")
    credentials = run([*compose, "exec", "-T", "minio", "sh", "-eu", "-c",
                       'printf "%s\\n%s\\n" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"']
                      ).stdout.decode().splitlines()
    if len(credentials) != 2 or any(not value or "\x00" in value for value in credentials):
        raise BackupError("Tracker object-store environment is invalid")
    user, password = credentials
    bucket = configured_bucket
    env_fd, env_name = tempfile.mkstemp(prefix=".tracker-mc-", dir=payload.parent)
    os.chmod(env_name, 0o600)
    try:
        with os.fdopen(env_fd, "w", encoding="utf-8") as handle:
            handle.write("MC_HOST_local=http://" + quote(user, safe="") + ":" + quote(password, safe="")
                         + "@127.0.0.1:9000\n")
            handle.flush()
            os.fsync(handle.fileno())
        listing = run(["docker", "run", "--rm", "--network", f"container:{minio_id}",
                       "--env-file", env_name, client_image, "ls", "--recursive", "--json",
                       f"local/{bucket}"]).stdout
        if len(listing) > 8 * 1024 * 1024:
            raise BackupError("Tracker object listing exceeds its size quota")
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for line in listing.splitlines():
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BackupError("Tracker object listing is invalid") from exc
            if (item.get("status") not in {None, "success"} or item.get("type") not in {None, "file"}):
                raise BackupError("Tracker object listing contains an error")
            key = _tracker_object_key(item.get("key"))
            size = item.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0 or key in seen:
                raise BackupError("Tracker object listing is invalid")
            seen.add(key)
            entries.append({"key": key, "bytes": size})
            if len(entries) > 4096:
                raise BackupError("Tracker object manifest exceeds its object quota")
        if expected_attachment_count > 0 and not entries:
            raise BackupError("Tracker attachments have no object-store entries")
        total_bytes = sum(item["bytes"] for item in entries)
        if total_bytes > 10 * 1024 * 1024 * 1024:
            raise BackupError("Tracker object manifest exceeds its size quota")
        objects: list[dict[str, Any]] = []
        for item in entries:
            temporary_fd, temporary_name = tempfile.mkstemp(prefix=".tracker-object-", dir=payload.parent)
            os.chmod(temporary_name, 0o600)
            try:
                with os.fdopen(temporary_fd, "wb") as handle:
                    run(["docker", "run", "--rm", "--network", f"container:{minio_id}",
                         "--env-file", env_name, client_image, "cat",
                         f"local/{bucket}/{item['key']}"], stdout=handle)
                temporary = Path(temporary_name)
                if temporary.stat().st_size != item["bytes"]:
                    raise BackupError("Tracker object size changed during export")
                objects.append({"key": item["key"], "bytes": item["bytes"], "sha256": sha256(temporary)})
            finally:
                Path(temporary_name).unlink(missing_ok=True)
    finally:
        Path(env_name).unlink(missing_ok=True)
    manifest = {"algorithm": TRACKER_OBJECT_MANIFEST_ALGORITHM, "bucket": bucket,
                "objects": objects, "object_count": len(objects),
                "total_bytes": sum(item["bytes"] for item in objects)}
    destination = payload / "files" / "object-manifest.json"
    atomic_json(destination, manifest, 0o600)
    return {"name": "files/object-manifest.json", "sha256": sha256(destination),
            "bytes": destination.stat().st_size}


@contextlib.contextmanager
def tracker_quiescence(app: dict[str, Any], before_minio: Any = None):
    services = app.get("quiesce_services")
    if (not isinstance(services, list) or not services
            or any(not isinstance(service, str) or not ID_RE.fullmatch(service) for service in services)):
        raise BackupError("Tracker quiescence service list is invalid")
    try:
        timeout = int(app.get("quiesce_timeout_seconds", 120))
    except (TypeError, ValueError) as exc:
        raise BackupError("Tracker quiescence timeout is invalid") from exc
    if not 1 <= timeout <= 900:
        raise BackupError("Tracker quiescence timeout is invalid")
    compose = compose_command(app)
    # Compose's ``stop`` accepts a service list, so first capture the active set.
    # This avoids starting an intentionally stopped worker or one-shot dependency
    # after an otherwise successful export.
    running_services = run([*compose, "ps", "--status", "running", "--services"], timeout=timeout).stdout.decode().splitlines()
    running_services = [service.strip() for service in running_services if service.strip()]
    if any(service not in services for service in running_services):
        raise BackupError("Tracker running orphan service detected")
    active: list[str] = []
    for service in services:
        container_id = ""
        if service in running_services:
            container_id = run([*compose, "ps", "--status", "running", "--quiet", service], timeout=timeout).stdout.decode().strip()
        if container_id:
            if not re.fullmatch(r"^[0-9a-f]{64}$", container_id):
                raise BackupError("Tracker running-service evidence is invalid")
            active.append(service)
    application_active = [service for service in active if service != "minio"]
    minio_active = [service for service in active if service == "minio"]
    try:
        if application_active:
            run([*compose, "stop", *application_active], timeout=timeout)
        if before_minio is not None:
            if not minio_active:
                raise BackupError("Tracker object-store service is not running")
            before_minio()
        if minio_active:
            run([*compose, "stop", *minio_active], timeout=timeout)
        yield
    finally:
        try:
            if active:
                run([*compose, "start", *active], timeout=timeout)
        except BackupError as exc:
            raise BackupError("Tracker services could not be restarted after backup") from exc


def compose_images(app: dict[str, Any]) -> list[str]:
    result = run([*compose_command(app), "config", "--images"])
    images = sorted(set(result.stdout.decode().splitlines()))
    if not images or any("@sha256:" not in image for image in images):
        raise BackupError("all application images must be pinned by digest")
    return images


def image_digests(images: list[str]) -> set[str]:
    digests = {image.rpartition("@sha256:")[2] for image in images}
    if any(not SHA_RE.fullmatch(digest) for digest in digests):
        raise BackupError("application image digest evidence is invalid")
    return digests


def compose_command(app: dict[str, Any]) -> list[str]:
    command = ["docker", "compose", "--project-name", app["compose_project"]]
    if app.get("adapter") == TRACKER_ADAPTER:
        env_file = app.get("compose_env_file")
        images_file = app.get("compose_images_file")
        if (not isinstance(env_file, str) or not env_file or not isinstance(images_file, str)
                or not images_file):
            raise BackupError("Tracker Compose environment and image files are required")
        command.extend(["--env-file", env_file])
    command.extend(["--file", app["compose_file"]])
    if app.get("adapter") == TRACKER_ADAPTER:
        command.extend(["--file", app["compose_images_file"]])
    return command


def running_compose_images(app: dict[str, Any]) -> dict[str, str]:
    approved = app.get("approved_images")
    if app.get("adapter") == TRACKER_ADAPTER:
        if (not isinstance(approved, dict) or not 2 <= len(approved) <= 32
                or any(not isinstance(service, str) or not ID_RE.fullmatch(service) for service in approved)
                or any(not isinstance(image, str) or not SHA_RE.fullmatch(image.rpartition("@sha256:")[2])
                       for image in approved.values())):
            raise BackupError("approved application image mapping is invalid")
        configured = image_digests(compose_images(app))
        if configured != image_digests(list(approved.values())):
            raise BackupError("compose image configuration does not match the approved digests")
        compose = compose_command(app)
        observed: dict[str, str] = {}
        runtime_services = app.get("runtime_image_services", list(approved))
        if (not isinstance(runtime_services, list) or not runtime_services
                or any(service not in approved for service in runtime_services)):
            raise BackupError("Tracker runtime service mapping is invalid")
        for service in runtime_services:
            expected = approved[service]
            container_id = run([*compose, "ps", "--quiet", service]).stdout.decode().strip()
            if not re.fullmatch(r"^[0-9a-f]{64}$", container_id):
                raise BackupError("approved compose service is not running")
            image_id = run(["docker", "inspect", "--format", "{{.Image}}", container_id]).stdout.decode().strip()
            if not re.fullmatch(r"^sha256:[0-9a-f]{64}$", image_id):
                raise BackupError("running container image identity is invalid")
            try:
                repo_digests = json.loads(run(
                    ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id]
                ).stdout)
            except json.JSONDecodeError as exc:
                raise BackupError("running container image digest evidence is invalid") from exc
            expected_digest = expected.rpartition("@sha256:")[2]
            if (not isinstance(repo_digests, list)
                    or not any(isinstance(value, str) and value.endswith(f"@sha256:{expected_digest}")
                               for value in repo_digests)):
                raise BackupError("running container does not use the approved image digest")
            observed[service] = expected
        return observed
    services = {app.get("postgres_service"), app.get("application_service")}
    if (not isinstance(approved, dict) or set(approved) != services
            or any(not isinstance(image, str) or not SHA_RE.fullmatch(image.rpartition("@sha256:")[2])
                   for image in approved.values())):
        raise BackupError("approved application image mapping is invalid")
    configured = set(compose_images(app))
    if configured != set(approved.values()):
        raise BackupError("compose image configuration does not match the approved digests")
    compose = compose_command(app)
    observed: dict[str, str] = {}
    for service, expected in approved.items():
        container_id = run([*compose, "ps", "--quiet", service]).stdout.decode().strip()
        if not re.fullmatch(r"^[0-9a-f]{64}$", container_id):
            raise BackupError("approved compose service is not running")
        image_id = run(["docker", "inspect", "--format", "{{.Image}}", container_id]).stdout.decode().strip()
        if not re.fullmatch(r"^sha256:[0-9a-f]{64}$", image_id):
            raise BackupError("running container image identity is invalid")
        try:
            repo_digests = json.loads(run(
                ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", image_id]
            ).stdout)
        except json.JSONDecodeError as exc:
            raise BackupError("running container image digest evidence is invalid") from exc
        expected_digest = expected.rpartition("@sha256:")[2]
        if (not isinstance(repo_digests, list)
                or not any(isinstance(value, str) and value.endswith(f"@sha256:{expected_digest}")
                           for value in repo_digests)):
            raise BackupError("running container does not use the approved image digest")
        observed[service] = expected
    return observed


def postgres_versions(app: dict[str, Any]) -> dict[str, Any]:
    compose = [*compose_command(app), "exec", "-T", app["postgres_service"], "sh", "-eu", "-c"]
    server = run([*compose, 'psql --tuples-only --no-align --username="$POSTGRES_USER" '
                           '--dbname="$POSTGRES_DB" --command="show server_version"']).stdout.decode().strip()
    dump = run([*compose, "pg_dump --version"]).stdout.decode().strip()
    if app.get("adapter") == TRACKER_ADAPTER:
        if not re.fullmatch(r"^17\.[0-9]+(?:\.[0-9]+)?$", server):
            raise BackupError("running PostgreSQL server version is not approved")
        if not re.fullmatch(r"^pg_dump \(PostgreSQL\) 17\.[0-9]+(?:\.[0-9]+)?$", dump):
            raise BackupError("running pg_dump version is not approved")
        migration_output = run([*compose, 'psql -X --tuples-only --no-align --set=ON_ERROR_STOP=1 '
                                      '--username="$POSTGRES_USER" --dbname="$POSTGRES_DB" '
                                      '--command="SELECT version FROM doctrine_migration_versions ORDER BY version"']).stdout
        migrations = [line.strip() for line in migration_output.decode().splitlines() if line.strip()]
        if not migrations or any(not re.fullmatch(r"[A-Za-z0-9_\\]+", value) for value in migrations):
            raise BackupError("Tracker migration ledger is invalid")
        content_output = run([*compose, f'psql -X --tuples-only --no-align --set=ON_ERROR_STOP=1 '
                                      f'--username="$POSTGRES_USER" --dbname="$POSTGRES_DB" '
                                      f'--command={json.dumps(TRACKER_CONTENT_MARKER_SQL)}']).stdout
        if not 1 <= len(content_output) <= 16 * 1024:
            raise BackupError("Tracker content marker is outside policy")
        try:
            content_marker = json.loads(content_output)
        except json.JSONDecodeError as exc:
            raise BackupError("Tracker content marker is invalid") from exc
        expected_content_keys = {f"{table}_count" for table in TRACKER_MARKER_TABLES}
        if (not isinstance(content_marker, dict) or set(content_marker) != expected_content_keys
                or any(not isinstance(content_marker[key], int) or isinstance(content_marker[key], bool)
                       or content_marker[key] < 0 for key in expected_content_keys)):
            raise BackupError("Tracker content marker is invalid")
        marker = {
            "algorithm": TRACKER_MIGRATION_MARKER_ALGORITHM,
            "sha256": hashlib.sha256(("\n".join(migrations) + "\n").encode()).hexdigest(),
            "migration_count": len(migrations),
            "row_counts": content_marker,
        }
        return {"engine": "postgresql", "server_version": server, "dump_version": dump,
                "dump_format": "custom", "service": app["postgres_service"],
                "content_marker": marker}
    if not re.fullmatch(r"^15\.[0-9]+(?:\.[0-9]+)?$", server):
        raise BackupError("running PostgreSQL server version is not approved")
    if not re.fullmatch(r"^pg_dump \(PostgreSQL\) 15\.[0-9]+(?:\.[0-9]+)?$", dump):
        raise BackupError("running pg_dump version is not approved")
    marker_output = run([*compose, f'psql -X --tuples-only --no-align --set=ON_ERROR_STOP=1 '
                                  f'--username="$POSTGRES_USER" --dbname="$POSTGRES_DB" '
                                  f'--command={json.dumps(UMAMI_CONTENT_MARKER_SQL)}']).stdout
    if not 1 <= len(marker_output) <= 16 * 1024:
        raise BackupError("database content marker is outside policy")
    try:
        marker = json.loads(marker_output)
    except json.JSONDecodeError as exc:
        raise BackupError("database content marker is invalid") from exc
    expected_keys = {"user_count", "two_factor_count", "admin", "admin_two_factor"}
    if not isinstance(marker, dict) or set(marker) != expected_keys:
        raise BackupError("database content marker does not prove the required Umami data")
    admin, two_factor = marker.get("admin"), marker.get("admin_two_factor")
    if (not isinstance(marker.get("user_count"), int) or isinstance(marker["user_count"], bool)
            or marker["user_count"] < 1
            or not isinstance(marker.get("two_factor_count"), int)
            or isinstance(marker["two_factor_count"], bool) or marker["two_factor_count"] < 1
            or not isinstance(admin, dict) or set(admin) != {"user_id", "username"}
            or admin.get("username") != "admin" or not isinstance(admin.get("user_id"), str)
            or not isinstance(two_factor, dict)
            or set(two_factor) != {"user_id", "is_enabled", "secret"}
            or two_factor.get("user_id") != admin["user_id"] or two_factor.get("is_enabled") is not True
            or not isinstance(two_factor.get("secret"), str) or not 1 <= len(two_factor["secret"]) <= 4096):
        raise BackupError("database content marker does not prove the required Umami data")
    return {"engine": "postgresql", "server_version": server, "dump_version": dump,
            "dump_format": "custom", "service": app["postgres_service"],
            "content_marker": {"algorithm": UMAMI_CONTENT_MARKER_ALGORITHM,
                               "sha256": hashlib.sha256(marker_output).hexdigest(),
                               "user_count": marker["user_count"],
                               "two_factor_count": marker["two_factor_count"]}}


def create_export(config: dict[str, Any], app_id: str) -> dict[str, Any]:
    with export_stage("application-contract"):
        host = require_id(config["host_slug"], "host slug")
        app = application(config, app_id)
    if app.get("adapter") not in {"postgres-compose-v1", TRACKER_ADAPTER}:
        raise BackupError("unsupported application adapter")
    spool = Path(config["spool_dir"])
    receipts = Path(config["receipt_dir"])
    work_root = Path(config["work_dir"])
    recipient = Path(config["age_recipient_file"])
    if (recipient.is_symlink() or not recipient.is_file() or recipient.stat().st_uid != ROOT_UID
            or stat.S_IMODE(recipient.stat().st_mode) != 0o600):
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
            with export_stage("compose-contract"):
                compose = compose_command(app)
                actual_images = running_compose_images(app)
            with export_stage("runtime-evidence"):
                database_versions = postgres_versions(app)
            inputs: list[dict[str, Any]] = []
            before_minio = None
            if app.get("adapter") == TRACKER_ADAPTER:
                expected_attachment_count = database_versions["content_marker"]["row_counts"]["attachment_count"]

                def capture_tracker_objects() -> None:
                    with export_stage("object-inventory"):
                        inputs.append(tracker_object_manifest(app, payload, expected_attachment_count))

                before_minio = capture_tracker_objects
            with export_stage("quiescence"):
                quiesce = (tracker_quiescence(app, before_minio=before_minio)
                           if app.get("adapter") == TRACKER_ADAPTER else contextlib.nullcontext())
                with quiesce:
                    with export_stage("database-export"):
                        database_dump = payload / "database.dump"
                        dump_command = compose + ["exec", "-T", app["postgres_service"], "sh", "-eu", "-c",
                            'exec pg_dump --format=custom --username="$POSTGRES_USER" "$POSTGRES_DB"']
                        with database_dump.open("xb") as handle:
                            run(dump_command, stdout=handle)
                        os.chmod(database_dump, 0o600)
                        if database_dump.stat().st_size == 0:
                            raise BackupError("database export is empty")
                        inputs.insert(0, {"name": "database.dump", "sha256": sha256(database_dump),
                                          "bytes": database_dump.stat().st_size})
                    with export_stage("config-metadata"):
                        for item in app["included_files"]:
                            logical = require_id(item["name"], "input name")
                            copied = copy_regular(Path(item["path"]), payload / "files" / logical)
                            copied["name"] = f"files/{logical}"
                            inputs.append(copied)
                        for item in app.get("included_directories", []):
                            logical = require_id(item["name"], "directory input name")
                            snapshot = payload / "files" / f"{logical}.tar"
                            snapshot_directory(Path(item["path"]), snapshot, int(item["max_bytes"]))
                            inputs.append({"name": f"files/{logical}.tar", "sha256": sha256(snapshot),
                                           "bytes": snapshot.stat().st_size})
            internal = {
                "schema_version": SCHEMA_VERSION, "artifact_id": artifact_id,
                "host_slug": host, "app_id": app_id, "adapter": app["adapter"],
                "created_at": now(), "database_service": app["postgres_service"],
                "images": sorted(actual_images.values()), "image_services": actual_images,
                "database": database_versions, "inputs": inputs,
            }
            if app.get("adapter") == TRACKER_ADAPTER:
                internal["object_store_bucket"] = app["object_store_bucket"]
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


def validate_receipt(value: Any, artifact_id: str, payload_sha256: str,
                     receipt_id: str, maximum_bytes: int) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != PUBLIC_MANIFEST_KEYS | {"received_at", "receipt_id"}:
        raise BackupError("backup receipt schema is invalid")
    public = {key: value[key] for key in PUBLIC_MANIFEST_KEYS}
    validate_public_manifest(public, maximum_bytes)
    if (public["artifact_id"] != artifact_id or public["payload_sha256"] != payload_sha256
            or value["receipt_id"] != receipt_id or not isinstance(value["received_at"], str)
            or not value["received_at"].endswith("Z")):
        raise BackupError("backup receipt identity is invalid")
    return value


def remove_published_export(spool: Path, manifest_path: Path, payload_path: Path) -> None:
    for target in (payload_path, manifest_path):
        if target.exists() or target.is_symlink():
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise BackupError("published export cleanup target is unsafe")
            target.unlink()
    fsync_directory(spool)


def dispatch(config: dict[str, Any], original: str, output: BinaryIO) -> None:
    parts = original.split()
    if parts == ["list"]:
        with lock(Path(config["lock_file"])):
            values = ready_manifests(config)
        output.write((json.dumps(values, separators=(",", ":")) + "\n").encode())
        return
    if len(parts) == 2 and parts[0] == "export":
        app_id = require_id(parts[1], "application id")
        value = create_export(config, app_id)
        output.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
        return
    if len(parts) == 2 and parts[0] == "fetch":
        artifact_id = require_artifact(parts[1])
        payload = Path(config["spool_dir"]) / f"{artifact_id}.age"
        with lock(Path(config["lock_file"])):
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
        ensure_directory(spool, 0o750)
        ensure_directory(Path(config["receipt_dir"]), 0o750)
        manifest_path, payload_path = spool / f"{artifact_id}.json", spool / f"{artifact_id}.age"
        receipt_path = Path(config["receipt_dir"]) / f"{artifact_id}.json"
        with lock(Path(config["lock_file"])):
            if receipt_path.exists() or receipt_path.is_symlink():
                if receipt_path.is_symlink() or not receipt_path.is_file():
                    raise BackupError("backup receipt is unsafe")
                receipt = validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")), artifact_id,
                                           parts[2], parts[3], int(config["spool_quota_bytes"]))
                public = {key: receipt[key] for key in PUBLIC_MANIFEST_KEYS}
                if manifest_path.exists() or manifest_path.is_symlink():
                    if manifest_path.is_symlink() or not manifest_path.is_file() \
                            or json.loads(manifest_path.read_text(encoding="utf-8")) != public:
                        raise BackupError("published export cleanup identity changed")
                if payload_path.exists() or payload_path.is_symlink():
                    if (payload_path.is_symlink() or not payload_path.is_file()
                            or payload_path.stat().st_size != public["payload_bytes"]
                            or sha256(payload_path) != public["payload_sha256"]):
                        raise BackupError("published export cleanup content changed")
                remove_published_export(spool, manifest_path, payload_path)
            else:
                if (manifest_path.is_symlink() or payload_path.is_symlink()
                        or not manifest_path.is_file() or not payload_path.is_file()):
                    raise BackupError("artifact is not ready")
                manifest = validate_public_manifest(json.loads(manifest_path.read_text(encoding="utf-8")),
                                                    int(config["spool_quota_bytes"]))
                if (manifest["artifact_id"] != artifact_id or manifest["payload_sha256"] != parts[2]
                        or sha256(payload_path) != parts[2]
                        or payload_path.stat().st_size != manifest["payload_bytes"]):
                    raise BackupError("acknowledgement checksum mismatch")
                receipt = {**manifest, "received_at": now(), "receipt_id": parts[3]}
                atomic_json(receipt_path, receipt)
                remove_published_export(spool, manifest_path, payload_path)
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
    reject_symlink_components(repository)
    reject_symlink_components(password)
    if (password.is_symlink() or not password.is_file() or password.stat().st_uid != ROOT_UID
            or stat.S_IMODE(password.stat().st_mode) != 0o600):
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
    if value.get("engine") != ENGINE_VERSION or value.get("integrity_coverage") != "full-repository-data":
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
        "engine": ENGINE_VERSION, "state": "available", "removed_at": None,
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
    if set(attempt) != keys and set(attempt) != keys | {"stage_code"}:
        raise BackupError("backup attempt schema is invalid")
    require_artifact(attempt.get("run_id", ""))
    require_trigger(attempt.get("trigger", ""))
    if (attempt.get("stage_code") is not None
            and not EXPORT_STAGE_RE.fullmatch(attempt["stage_code"])):
        raise BackupError("backup attempt stage code is invalid")
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
               "finished_at": None, "status": "running", "error_code": None, "stage_code": None}
    value["latest_attempt"] = attempt
    write_catalog(config, value)
    return attempt


def attempt_finish(config: dict[str, Any], host_slug: str, app_id: str, run_id: str,
                   status: str, error_code: str | None, stage_code: str | None = None) -> dict[str, Any]:
    require_artifact(run_id)
    if status not in {"failed", "succeeded", "uncertain"} or error_code not in ERROR_CODES:
        raise BackupError("invalid backup attempt result")
    if stage_code is not None and not EXPORT_STAGE_RE.fullmatch(stage_code):
        raise BackupError("invalid backup attempt stage code")
    if status == "succeeded" and stage_code is not None:
        raise BackupError("successful backup attempt has a failure stage")
    if (status == "succeeded") != (error_code is None):
        raise BackupError("backup attempt status and error code disagree")
    value = load_catalog(config, host_slug, app_id)
    attempt = value.get("latest_attempt")
    if not isinstance(attempt, dict) or attempt.get("run_id") != run_id or attempt.get("status") != "running":
        raise BackupError("backup attempt result does not match active run")
    if status == "succeeded":
        durable = [point for point in value["recovery_points"] if point.get("state") == "available"
                   and point.get("run_id") == run_id and point.get("host_slug") == host_slug
                   and point.get("app_id") == app_id and point.get("integrity_coverage") == "full-repository-data"]
        if len(durable) != 1:
            raise BackupError("successful backup attempt lacks exact durable snapshot evidence")
    attempt.update({"finished_at": now(), "status": status, "error_code": error_code,
                    "stage_code": stage_code})
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


def stage_remote_artifact(config: dict[str, Any], host: dict[str, Any],
                          manifest: dict[str, Any]) -> Path:
    artifact_id = manifest["artifact_id"]
    incoming_root = Path(config["incoming_root"])
    ensure_private_runtime_directory(incoming_root)
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
                output.flush()
                os.fsync(output.fileno())
            if sha256(payload) != manifest["payload_sha256"] or payload.stat().st_size != manifest["payload_bytes"]:
                raise BackupError("received artifact failed checksum or size validation")
            atomic_json(temporary / "manifest.json", manifest, 0o600)
            os.replace(temporary, incoming)
            fsync_directory(incoming_root)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    else:
        info = incoming.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise BackupError("incoming artifact is not a controlled directory")
        members = {item.name for item in incoming.iterdir()}
        if members != {"manifest.json", "payload.age"}:
            raise BackupError("incoming artifact has unexpected members")
        manifest_path, payload_path = incoming / "manifest.json", incoming / "payload.age"
        if (manifest_path.is_symlink() or payload_path.is_symlink()
                or not manifest_path.is_file() or not payload_path.is_file()):
            raise BackupError("incoming artifact members are unsafe")
        local_manifest = validate_public_manifest(json.loads(manifest_path.read_text(encoding="utf-8")),
                                                  int(config["max_artifact_bytes"]))
        if local_manifest != manifest:
            raise BackupError("incoming artifact identity changed during retry")
        if (payload_path.stat().st_size != manifest["payload_bytes"]
                or sha256(payload_path) != manifest["payload_sha256"]):
            raise BackupError("incoming artifact content changed during retry")
    return incoming


def cleanup_worker_staging(config: dict[str, Any], artifact_id: str) -> None:
    artifact_id = require_artifact(artifact_id)
    incoming_root = Path(config["incoming_root"])
    target = incoming_root / artifact_id
    reject_symlink_components(incoming_root)
    if target.exists() or target.is_symlink():
        info = target.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise BackupError("incoming artifact cleanup target is unsafe")
        shutil.rmtree(target)
        fsync_directory(incoming_root)


def pull_host(config: dict[str, Any], host_slug: str, run_id: str | None = None,
              trigger: str = "manual", expected_artifact_id: str | None = None) -> list[dict[str, Any]]:
    require_id(host_slug, "host slug")
    matches = [item for item in config["hosts"] if item["host_slug"] == host_slug]
    if len(matches) != 1:
        raise BackupError("host is not allowlisted")
    host = matches[0]
    run_id = require_artifact(run_id or str(uuid.uuid4()))
    if expected_artifact_id is not None:
        require_artifact(expected_artifact_id)
    trigger = require_trigger(trigger)
    if not host["applications"]:
        return [{"status": "not-applicable", "host_slug": host_slug,
                 "reason": "no-enabled-production-applications"}]
    received = []
    Path(config["work_dir"]).mkdir(parents=True, exist_ok=True)
    with lock(Path(config["pull_lock_root"]) / f"{host_slug}.lock"):
        manifests = ssh_json(host, "list")
        if not isinstance(manifests, list) or len(manifests) > 1000:
            raise BackupError("remote artifact listing is invalid")
        for manifest in manifests:
            if not isinstance(manifest, dict):
                raise BackupError("remote artifact listing is invalid")
            manifest = validate_public_manifest(manifest, int(config["max_artifact_bytes"]))
            artifact_id = require_artifact(manifest["artifact_id"])
            app_id = require_id(manifest["app_id"], "application id")
            if manifest["host_slug"] != host_slug or app_id not in host["applications"]:
                raise BackupError("remote artifact identity is not allowlisted")
            incoming = stage_remote_artifact(config, host, manifest)
            artifact_run_id = run_id if expected_artifact_id is None or artifact_id == expected_artifact_id \
                else str(uuid.uuid4())
            stored = validate_durable_result(
                manifest, ingest_artifact(config, host_slug, app_id, artifact_id, artifact_run_id, trigger)
            )
            snapshot_id = stored["snapshot_id"]
            receipt_id = f"restic:{snapshot_id}"
            # Local ciphertext staging is removed before the remote ACK. If cleanup or ACK
            # fails, the source artifact stays listable (or has an idempotent receipt), and
            # the terminal attempt exposes the failure without deleting the durable point.
            cleanup_ingest(config, artifact_id)
            cleanup_worker_staging(config, artifact_id)
            ssh_json(host, f'ack {artifact_id} {manifest["payload_sha256"]} {receipt_id}')
            received.append(stored)
    return received


def ingest_artifact(config: dict[str, Any], host_slug: str, app_id: str, artifact_id: str,
                    run_id: str, trigger: str, safety_operation: str | None = None) -> dict[str, Any]:
    require_artifact(artifact_id)
    assert_allowlisted_target(config, host_slug, app_id)
    operation_id = require_id(safety_operation, "operation id") if safety_operation is not None else None
    safety_tags = ([SAFETY_TAG, f"operation:{operation_id}"]
                   if operation_id is not None else [])
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
        if not isinstance(known, list) or len(known) > CATALOG_LIMIT:
            raise BackupError("restic artifact lookup returned invalid evidence")
        if known:
            expected_tags = set(safety_tags)
            if expected_tags and (len(known) != 1
                                  or any(not expected_tags.issubset(set(item.get("tags", []))) for item in known)):
                raise BackupError("existing artifact does not have the approved safety pin identity")
            snapshot_id = known[-1]["id"]
        else:
            tags = [f"host:{host_slug}", f"app:{app_id}", f"artifact:{artifact_id}", *safety_tags]
            backup_command = ["restic", "backup", "--json"]
            for tag in tags:
                backup_command.extend(["--tag", tag])
            result = run([*backup_command, str(incoming)], env=env)
            messages = [json.loads(line) for line in result.stdout.decode().splitlines() if line.startswith("{")]
            summaries = [item for item in messages if item.get("message_type") == "summary"]
            if not summaries or not summaries[-1].get("snapshot_id"):
                raise BackupError("restic did not return a durable snapshot id")
            snapshot_id = summaries[-1]["snapshot_id"]
        if not SHA_RE.fullmatch(snapshot_id):
            raise BackupError("restic did not return a full snapshot id")
        run(["restic", "check", "--read-data"], env=env)
        stored_at, checked_at = now(), now()
        record_catalog_point(config, manifest, snapshot_id, stored_at, checked_at, run_id, trigger)
        return {**manifest, "snapshot_id": snapshot_id, "stored_at": stored_at, "integrity_checked_at": checked_at,
                "integrity_coverage": "full-repository-data"}


def safety_ingest(config: dict[str, Any], host_slug: str, app_id: str, artifact_id: str,
                  operation_id: str, run_id: str, inherited_host_lock_fd: int | None = None) -> dict[str, Any]:
    operation_id = require_id(operation_id, "operation id")
    with host_operation_lock(config, host_slug, inherited_host_lock_fd):
        stored = ingest_artifact(config, host_slug, app_id, artifact_id, require_artifact(run_id), "console",
                                 safety_operation=operation_id)
    return safety_result(stored, operation_id)


def safety_result(stored: dict[str, Any], operation_id: str) -> dict[str, Any]:
    return {
        "status": "safety-stored", "host_slug": stored["host_slug"], "app_id": stored["app_id"],
        "artifact_id": stored["artifact_id"], "operation_id": operation_id,
        "snapshot_id": stored["snapshot_id"],
        "tags": [SAFETY_TAG, f"operation:{operation_id}"],
        "integrity_checked_at": stored["integrity_checked_at"],
        "integrity_coverage": stored["integrity_coverage"],
    }


def safety_pull(config: dict[str, Any], host_slug: str, app_id: str, artifact_id: str,
                operation_id: str, run_id: str, inherited_host_lock_fd: int | None = None) -> dict[str, Any]:
    require_artifact(artifact_id)
    require_artifact(run_id)
    operation_id = require_id(operation_id, "operation id")
    host = assert_allowlisted_target(config, host_slug, app_id)
    with (host_operation_lock(config, host_slug, inherited_host_lock_fd),
          lock(Path(config["pull_lock_root"]) / f"{host_slug}.lock")):
        reconciled = reconcile_safety_point(config, host_slug, app_id, artifact_id, operation_id)
        if reconciled is not None:
            result, payload_sha256 = reconciled
            ssh_json(host, f'ack {artifact_id} {payload_sha256} restic:{result["snapshot_id"]}')
            return result
        listed = ssh_json(host, "list")
        if not isinstance(listed, list) or len(listed) > 1000:
            raise BackupError("remote artifact listing is invalid")
        matches = [value for value in listed if isinstance(value, dict) and value.get("artifact_id") == artifact_id]
        if len(matches) != 1:
            raise BackupError("requested safety artifact is unavailable or ambiguous")
        manifest = validate_public_manifest(matches[0], int(config["max_artifact_bytes"]))
        if manifest["host_slug"] != host_slug or manifest["app_id"] != app_id:
            raise BackupError("remote safety artifact identity is not allowlisted")
        incoming = stage_remote_artifact(config, host, manifest)
        stored = ingest_artifact(config, host_slug, app_id, artifact_id, run_id, "console",
                                 safety_operation=operation_id)
        cleanup_ingest(config, artifact_id)
        cleanup_worker_staging(config, artifact_id)
        ssh_json(host, f'ack {artifact_id} {manifest["payload_sha256"]} restic:{stored["snapshot_id"]}')
        return safety_result(stored, operation_id)


def reconcile_safety_point(config: dict[str, Any], host_slug: str, app_id: str,
                           artifact_id: str, operation_id: str) -> tuple[dict[str, Any], str] | None:
    env, repository = restic_env(config, host_slug, app_id)
    if not (repository / "config").is_file():
        return None
    known = json.loads(run(
        ["restic", "snapshots", "--json", "--tag", f"artifact:{artifact_id}"], env=env
    ).stdout)
    if not isinstance(known, list) or len(known) > CATALOG_LIMIT:
        raise BackupError("restic safety lookup returned invalid evidence")
    required_tags = {SAFETY_TAG, f"operation:{operation_id}"}
    matching = [item for item in known if isinstance(item, dict) and SHA_RE.fullmatch(item.get("id", ""))
                and required_tags.issubset(set(item.get("tags", [])))]
    if not matching:
        return None
    if len(matching) != 1:
        raise BackupError("durable safety artifact identity is ambiguous")
    snapshot_id = matching[0]["id"]
    points = [point for point in load_catalog(config, host_slug, app_id)["recovery_points"]
              if point["state"] == "available" and point["snapshot_id"] == snapshot_id
              and point["artifact_id"] == artifact_id]
    if len(points) != 1:
        raise BackupError("durable safety artifact lacks exact catalog evidence")
    run(["restic", "check", "--read-data"], env=env)
    stored = {**points[0], "integrity_checked_at": now(), "integrity_coverage": "full-repository-data"}
    return safety_result(stored, operation_id), points[0]["payload_sha256"]


def cleanup_ingest(config: dict[str, Any], artifact_id: str) -> None:
    require_artifact(artifact_id)
    ingest_root = Path(config["ingest_root"])
    ensure_private_runtime_directory(ingest_root)
    target = ingest_root / artifact_id
    if target.exists():
        if target.is_symlink() or not target.is_dir() or target.stat().st_uid != os.geteuid():
            raise BackupError("privileged ingest cleanup target is not controlled")
        shutil.rmtree(target)
        fsync_directory(ingest_root)


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
        attempt = attempt_start(config, host_slug, app_id, trigger)
        validate_attempt(attempt)
        run_id = attempt["run_id"]
        try:
            policy = load_policy(config, host_slug, app_id)
            if policy["policy_version"] != policy_version or policy["plan_hash"] != plan_hash:
                raise BackupError("active policy does not match approved backup cycle")
            exported = validate_public_manifest(ssh_json(host, f"export {app_id}"),
                                                int(config["max_artifact_bytes"]))
            if exported["host_slug"] != host_slug or exported["app_id"] != app_id:
                raise BackupError("export identity does not match the approved backup cycle")
            stored = pull_host(config, host_slug, run_id, trigger, exported["artifact_id"])
            matches = [item for item in stored if item.get("artifact_id") == exported["artifact_id"]]
            if len(matches) != 1:
                raise BackupError("export did not reach durable checked storage")
            result = matches[0]
        except Exception as exc:
            attempt_finish(config, host_slug, app_id, run_id, "failed", failure_code(exc), failure_stage(exc))
            raise
        attempt_finish(config, host_slug, app_id, run_id, "succeeded", None)
        return {**result, "run_id": run_id, "trigger": trigger}


def failure_stage(exc: Exception) -> str | None:
    match = EXPORT_FAILURE_RE.search(str(exc))
    return match.group(1) if match else None


def failure_code(exc: Exception) -> str:
    message = str(exc).lower()
    if "quota" in message or "filesystem is full" in message or "outside policy" in message:
        return "capacity_exhausted"
    if "source export failed" in message:
        return "source_export_failed"
    if "external command failed: ssh" in message or "offline" in message or "unreachable" in message:
        return "source_unreachable"
    if "checksum" in message or "integrity" in message:
        return "transfer_integrity"
    if "active operation" in message or "operation lock" in message:
        return "operation_busy"
    if "policy" in message:
        return "policy_invalid"
    return "backup_failed"


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


def run_active(config: dict[str, Any], host_slug: str, app_id: str, trigger: str) -> dict[str, Any]:
    assert_allowlisted_target(config, host_slug, app_id)
    policy = load_policy(config, host_slug, app_id)
    return cycle(config, host_slug, app_id, policy["policy_version"], policy["plan_hash"], trigger)


def safe_extract(archive: Path, target: Path, maximum_bytes: int | None = None) -> None:
    with tarfile.open(archive, "r") as tar:
        members = tar.getmembers()
        limit = archive.stat().st_size if maximum_bytes is None else maximum_bytes
        if len(members) > 256 or sum(member.size for member in members) > limit:
            raise BackupError("archive exceeds restore policy")
        names: set[str] = set()
        for member in members:
            relative = PurePosixPath(member.name)
            if (relative.is_absolute() or str(relative) != member.name or ".." in relative.parts
                    or member.name in names or member.issym() or member.islnk() or member.isdev()
                    or not (member.isfile() or member.isdir())):
                raise BackupError("unsafe archive member")
            names.add(member.name)
        tar.extractall(target, filter="data")


def validated_input_path(payload_root: Path, name: Any) -> Path:
    if (not isinstance(name, str) or not name or len(name) > 255 or "\\" in name
            or "\x00" in name):
        raise BackupError("encrypted payload input name is invalid")
    relative = PurePosixPath(name)
    if relative.is_absolute() or str(relative) != name or any(part in {"", ".", ".."} for part in relative.parts):
        raise BackupError("encrypted payload input name is invalid")
    current = payload_root
    for part in relative.parts:
        current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise BackupError("encrypted payload input is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise BackupError("encrypted payload input contains a symbolic link")
    if not stat.S_ISREG(current.lstat().st_mode):
        raise BackupError("encrypted payload input must be a regular file")
    return current


def validate_payload(root: Path, expected: dict[str, Any]) -> dict[str, Any]:
    payload_root = root / "payload"
    if payload_root.is_symlink() or not payload_root.is_dir():
        raise BackupError("encrypted payload root is invalid")
    manifest_path = validated_input_path(payload_root, "backup-manifest.json")
    internal = json.loads(manifest_path.read_text(encoding="utf-8"))
    keys = set(internal) if isinstance(internal, dict) else set()
    allowed_keys = (INTERNAL_MANIFEST_BASE_KEYS | INTERNAL_MANIFEST_VERSION_KEYS,
                    INTERNAL_MANIFEST_BASE_KEYS | INTERNAL_MANIFEST_TRACKER_KEYS)
    if keys not in allowed_keys:
        raise BackupError("encrypted payload manifest schema is invalid")
    for key in ("schema_version", "artifact_id", "host_slug", "app_id", "adapter", "created_at"):
        if internal.get(key) != expected.get(key):
            raise BackupError("encrypted payload identity mismatch")
    if (not isinstance(internal.get("created_at"), str) or not internal["created_at"].endswith("Z")
            or not isinstance(internal.get("database_service"), str)
            or internal.get("adapter") not in {"postgres-compose-v1", TRACKER_ADAPTER}):
        raise BackupError("encrypted payload manifest content is invalid")
    require_id(internal["database_service"], "database service")
    validate_materialized_images(internal.get("images"))
    if internal["adapter"] == TRACKER_ADAPTER:
        bucket = internal.get("object_store_bucket")
        if not isinstance(bucket, str) or not ID_RE.fullmatch(bucket):
            raise BackupError("encrypted payload object-store bucket is invalid")
        object_manifest = validated_input_path(payload_root, "files/object-manifest.json")
        _tracker_object_manifest(object_manifest, bucket)
    if INTERNAL_MANIFEST_VERSION_KEYS.issubset(keys):
        services = internal["image_services"]
        database = internal["database"]
        if (not isinstance(services, dict) or not 1 <= len(services) <= 16
                or any(not isinstance(service, str) or not ID_RE.fullmatch(service) for service in services)
                or internal["database_service"] not in services
                or sorted(services.values()) != sorted(internal["images"])):
            raise BackupError("encrypted payload image evidence is invalid")
        expected_database_keys = {
            "engine", "server_version", "dump_version", "dump_format", "service", "content_marker",
        }
        marker = database.get("content_marker") if isinstance(database, dict) else None
        database_shape_valid = (isinstance(database, dict) and set(database) == expected_database_keys
                                and database.get("engine") == "postgresql"
                                and database.get("service") == internal["database_service"]
                                and database.get("dump_format") == "custom")
        if not database_shape_valid:
            raise BackupError("encrypted payload database version evidence is invalid")
        if internal["adapter"] == TRACKER_ADAPTER:
            if (not re.fullmatch(r"^17\.[0-9]+(?:\.[0-9]+)?$", database["server_version"])
                    or not re.fullmatch(r"^pg_dump \(PostgreSQL\) 17\.[0-9]+(?:\.[0-9]+)?$", database["dump_version"])
                    or not isinstance(marker, dict)
                    or set(marker) != {"algorithm", "sha256", "migration_count", "row_counts"}
                    or marker.get("algorithm") != TRACKER_MIGRATION_MARKER_ALGORITHM
                    or not isinstance(marker.get("sha256"), str) or not SHA_RE.fullmatch(marker["sha256"])
                    or not isinstance(marker.get("migration_count"), int)
                    or isinstance(marker["migration_count"], bool) or marker["migration_count"] < 1
                    or not isinstance(marker.get("row_counts"), dict)
                    or set(marker["row_counts"]) != {f"{table}_count" for table in TRACKER_MARKER_TABLES}
                    or any(not isinstance(count, int) or isinstance(count, bool) or count < 0
                           for count in marker["row_counts"].values())):
                raise BackupError("encrypted payload database version evidence is invalid")
        elif (not re.fullmatch(r"^15\.[0-9]+(?:\.[0-9]+)?$", database["server_version"])
              or not re.fullmatch(r"^pg_dump \(PostgreSQL\) 15\.[0-9]+(?:\.[0-9]+)?$", database["dump_version"])
              or not isinstance(marker, dict)
              or set(marker) != {"algorithm", "sha256", "user_count", "two_factor_count"}
              or marker.get("algorithm") != UMAMI_CONTENT_MARKER_ALGORITHM
              or not isinstance(marker.get("sha256"), str) or not SHA_RE.fullmatch(marker["sha256"])
              or not isinstance(marker.get("user_count"), int) or isinstance(marker["user_count"], bool)
              or marker["user_count"] < 1
              or not isinstance(marker.get("two_factor_count"), int)
              or isinstance(marker["two_factor_count"], bool) or marker["two_factor_count"] < 1):
            raise BackupError("encrypted payload database version evidence is invalid")
    inputs = internal.get("inputs")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 128:
        raise BackupError("encrypted payload input list is invalid")
    names: set[str] = set()
    for item in inputs:
        if (not isinstance(item, dict) or set(item) != INPUT_KEYS or not isinstance(item.get("name"), str)
                or not isinstance(item.get("bytes"), int)
                or isinstance(item.get("bytes"), bool) or item["bytes"] < 0
                or not isinstance(item.get("sha256"), str) or not SHA_RE.fullmatch(item["sha256"])):
            raise BackupError("encrypted payload input schema is invalid")
        if item["name"] in names:
            raise BackupError("encrypted payload input names must be unique")
        names.add(item["name"])
        source = validated_input_path(payload_root, item["name"])
        if source.stat().st_size != item["bytes"] or sha256(source) != item["sha256"]:
            raise BackupError("encrypted payload input checksum mismatch")
    actual_files: set[str] = set()
    for candidate in payload_root.rglob("*"):
        relative = candidate.relative_to(payload_root).as_posix()
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise BackupError("encrypted payload contains a symbolic link")
        if stat.S_ISREG(info.st_mode):
            actual_files.add(relative)
        elif not stat.S_ISDIR(info.st_mode):
            raise BackupError("encrypted payload contains an unsupported file type")
    if actual_files != names | {"backup-manifest.json"}:
        raise BackupError("encrypted payload contains unmanifested files")
    return internal


def resolve_restore_point(config: dict[str, Any], host_slug: str, app_id: str,
                          selector: str) -> dict[str, Any]:
    available = [point for point in load_catalog(config, host_slug, app_id)["recovery_points"]
                 if point["state"] == "available"]
    if selector == "latest":
        matches = sorted(available, key=lambda point: point["stored_at"], reverse=True)[:1]
    else:
        matches = [point for point in available if point["snapshot_id"].startswith(selector)]
    if len(matches) != 1:
        raise BackupError("restore snapshot selector is unavailable or ambiguous")
    return matches[0]


def validate_restore_adapter_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != RESTORE_ADAPTER_KEYS:
        raise BackupError("restore adapter evidence schema is invalid")
    if (value.get("schema_version") != SCHEMA_VERSION
            or value.get("adapter") != "postgres-compose-v1"
            or value.get("application") != "umami" or value.get("status") != "passed"):
        raise BackupError("restore adapter evidence identity is invalid")
    if (not isinstance(value.get("public_table_count"), int)
            or isinstance(value["public_table_count"], bool) or value["public_table_count"] < 1):
        raise BackupError("restore adapter schema evidence is invalid")
    if (value.get("application_health") != "passed" or value.get("encrypted_secret_validation") != "passed"
            or value.get("database_content_marker") != "passed"
            or value.get("network") != "loopback-only-network-namespace"
            or value.get("external_effects_blocked") is not True or value.get("host_ports") != 0):
        raise BackupError("restore adapter isolation or health evidence is invalid")
    if (not isinstance(value.get("postgres_image"), str) or "@sha256:" not in value["postgres_image"]
            or not isinstance(value.get("application_image"), str)
            or "@sha256:" not in value["application_image"]):
        raise BackupError("restore adapter version evidence is invalid")
    if value.get("containers_removed") is not True or value.get("workspace_removed") is not True:
        raise BackupError("restore adapter cleanup did not succeed")
    return value


def validate_tracker_restore_adapter_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != TRACKER_RESTORE_ADAPTER_KEYS:
        raise BackupError("restore adapter evidence schema is invalid")
    if (value.get("schema_version") != SCHEMA_VERSION
            or value.get("adapter") != TRACKER_ADAPTER
            or value.get("application") != "tracker" or value.get("status") != "passed"):
        raise BackupError("restore adapter evidence identity is invalid")
    if (not isinstance(value.get("public_table_count"), int)
            or isinstance(value["public_table_count"], bool) or value["public_table_count"] < 1):
        raise BackupError("restore adapter schema evidence is invalid")
    if (value.get("application_health") != "not-run-external-effects-blocked"
            or value.get("encrypted_secret_validation") != "not-applicable"
            or value.get("database_content_marker") != "passed"
            or value.get("object_store_reconciliation") != "passed"
            or value.get("network") != "loopback-only-network-namespace"
            or value.get("external_effects_blocked") is not True or value.get("host_ports") != 0):
        raise BackupError("restore adapter isolation or health evidence is invalid")
    if (not isinstance(value.get("postgres_image"), str) or "@sha256:" not in value["postgres_image"]
            or not isinstance(value.get("application_image"), str)
            or "@sha256:" not in value["application_image"]):
        raise BackupError("restore adapter version evidence is invalid")
    if value.get("containers_removed") is not True or value.get("workspace_removed") is not True:
        raise BackupError("restore adapter cleanup did not succeed")
    return value


def restore_adapter_path(config: dict[str, Any], host_slug: str, app_id: str,
                         adapter_name: str) -> str:
    configured = config.get("restore_adapters", {})
    if isinstance(configured, dict) and isinstance(configured.get(adapter_name), str):
        return configured[adapter_name]
    # Keep the original profile contract for Umami. Tracker must be explicitly
    # installed because it has different data and isolation checks.
    if adapter_name == "postgres-compose-v1":
        return config["restore_adapter"]
    raise BackupError("restore adapter is not configured")


def validate_materialized_images(value: Any) -> list[str]:
    if (not isinstance(value, list) or not 1 <= len(value) <= 16
            or any(not isinstance(image, str) or len(image) > 512 or "@sha256:" not in image for image in value)):
        raise BackupError("materialized image version evidence is invalid")
    return value


def materialization_result(internal: dict[str, Any], snapshot_id: str, operation_id: str,
                           purpose: str) -> dict[str, Any]:
    images = validate_materialized_images(internal.get("images"))
    manifest = internal["manifest_path"]
    return {
        "status": "materialized", "host_slug": internal["host_slug"], "app_id": internal["app_id"],
        "snapshot_id": snapshot_id, "operation_id": operation_id, "purpose": purpose,
        "artifact_id": internal["artifact_id"], "manifest_sha256": sha256(manifest),
        "payload_sha256": internal["payload_sha256"], "adapter": internal["adapter"], "images": images,
    }


def validate_existing_materialization(final: Path, host_slug: str, app_id: str, snapshot_id: str,
                                      operation_id: str, purpose: str) -> dict[str, Any]:
    info = final.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise BackupError("materialized target is unsafe")
    binding_path = final / ".materialization.json"
    if binding_path.is_symlink() or not binding_path.is_file():
        raise BackupError("materialized target binding is unavailable")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    expected = {"host_slug": host_slug, "app_id": app_id, "snapshot_id": snapshot_id,
                "operation_id": operation_id, "purpose": purpose}
    if any(binding.get(key) != value for key, value in expected.items()):
        raise BackupError("materialized target binding conflicts with the approved operation")
    manifest_path = final / "payload" / "backup-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise BackupError("materialized manifest is unavailable")
    internal = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_payload(final, internal)
    internal.update({"manifest_path": manifest_path, "payload_sha256": binding.get("payload_sha256")})
    result = materialization_result(internal, snapshot_id, operation_id, purpose)
    if binding != result:
        raise BackupError("materialized target binding failed integrity validation")
    return result


def materialize_snapshot(config: dict[str, Any], host_slug: str, app_id: str, snapshot_id: str,
                         operation_id: str, purpose: str,
                         inherited_host_lock_fd: int | None = None) -> dict[str, Any]:
    if not SHA_RE.fullmatch(snapshot_id):
        raise BackupError("materialize requires a full snapshot id")
    operation_id = require_id(operation_id, "operation id")
    if purpose not in {"restore", "rollback"}:
        raise BackupError("invalid materialization purpose")
    assert_allowlisted_target(config, host_slug, app_id)
    identity = Path(config["age_identity_file"])
    if identity.is_symlink() or not identity.is_file() or identity.stat().st_uid != ROOT_UID \
            or stat.S_IMODE(identity.stat().st_mode) != 0o600:
        raise BackupError("age restore identity is unavailable")
    env, _ = restic_env(config, host_slug, app_id)
    materialized_root = Path(config["materialized_root"])
    ensure_private_runtime_directory(materialized_root)
    operation_root = materialized_root / operation_id
    ensure_private_runtime_directory(operation_root)
    final = operation_root / purpose
    with (host_operation_lock(config, host_slug, inherited_host_lock_fd),
          lock(operation_lock_path(config, host_slug, app_id)),
          lock(Path(config["restore_lock_file"]))):
        point = resolve_restore_point(config, host_slug, app_id, snapshot_id)
        if final.exists() or final.is_symlink():
            return validate_existing_materialization(final, host_slug, app_id, snapshot_id,
                                                     operation_id, purpose)
        required_bytes = max(RESTORE_MIN_FREE_BYTES, point["payload_bytes"] * RESTORE_SPACE_MULTIPLIER)
        if shutil.disk_usage(materialized_root).free < required_bytes:
            raise BackupError("materialization capacity is below the required minimum")
        with tempfile.TemporaryDirectory(prefix=f".{purpose}-", dir=operation_root) as temporary:
            root = Path(temporary)
            restored = root / "restic"
            run(["restic", "restore", snapshot_id, "--target", str(restored)], env=env)
            run(["restic", "check", "--read-data"], env=env)
            manifests = list(restored.rglob("manifest.json"))
            payloads = list(restored.rglob("payload.age"))
            if len(manifests) != 1 or len(payloads) != 1:
                raise BackupError("snapshot does not contain exactly one artifact")
            public = validate_public_manifest(json.loads(manifests[0].read_text(encoding="utf-8")),
                                              int(config["max_artifact_bytes"]))
            if (public["host_slug"] != host_slug or public["app_id"] != app_id
                    or public["artifact_id"] != point["artifact_id"]
                    or public["payload_sha256"] != point["payload_sha256"]
                    or public["payload_bytes"] != point["payload_bytes"]):
                raise BackupError("snapshot catalog identity mismatch")
            if sha256(payloads[0]) != public["payload_sha256"]:
                raise BackupError("snapshot payload checksum mismatch")
            archive = root / "payload.tar"
            run(["age", "--decrypt", "--identity", str(identity), "--output", str(archive),
                 str(payloads[0])])
            extracted = root / "extracted"
            extracted.mkdir(mode=0o700)
            safe_extract(archive, extracted, int(config["max_artifact_bytes"]))
            internal = validate_payload(extracted, public)
            manifest_path = extracted / "payload" / "backup-manifest.json"
            internal.update({"manifest_path": manifest_path, "payload_sha256": public["payload_sha256"]})
            result = materialization_result(internal, snapshot_id, operation_id, purpose)
            atomic_json(extracted / ".materialization.json", result, 0o600)
            os.replace(extracted, final)
            directory_fd = os.open(operation_root, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        if final.is_symlink() or not final.is_dir():
            raise BackupError("materialized target publication failed")
        return validate_existing_materialization(final, host_slug, app_id, snapshot_id,
                                                 operation_id, purpose)


def restore_test(config: dict[str, Any], host_slug: str, app_id: str, snapshot: str) -> dict[str, Any]:
    if snapshot != "latest" and not re.fullmatch(r"^[0-9a-f]{8,64}$", snapshot):
        raise BackupError("invalid snapshot id")
    assert_allowlisted_target(config, host_slug, app_id)
    env, _ = restic_env(config, host_slug, app_id)
    restore_work = Path(config["restore_work_dir"])
    ensure_private_runtime_directory(restore_work)
    identity = Path(config["age_identity_file"])
    if identity.is_symlink() or not identity.is_file() or identity.stat().st_uid != ROOT_UID \
            or stat.S_IMODE(identity.stat().st_mode) != 0o600:
        raise BackupError("age restore identity is unavailable")
    started = time.monotonic()
    with (host_operation_lock(config, host_slug), lock(operation_lock_path(config, host_slug, app_id)),
          lock(Path(config["restore_lock_file"]))):
        point = resolve_restore_point(config, host_slug, app_id, snapshot)
        snapshot_id = point["snapshot_id"]
        required_bytes = max(RESTORE_MIN_FREE_BYTES, point["payload_bytes"] * RESTORE_SPACE_MULTIPLIER)
        available_bytes = shutil.disk_usage(restore_work).free
        if available_bytes < required_bytes:
            raise BackupError("restore workspace capacity is below the required minimum")
        public: dict[str, Any]
        internal: dict[str, Any]
        adapter: dict[str, Any]
        manifest_sha256: str
        temporary_path: Path
        with tempfile.TemporaryDirectory(prefix="restore-", dir=restore_work) as temporary:
            temporary_path = Path(temporary)
            root = temporary_path
            restored = root / "restic"
            run(["restic", "check", "--read-data"], env=env)
            run(["restic", "restore", snapshot_id, "--target", str(restored)], env=env)
            manifests = list(restored.rglob("manifest.json"))
            payloads = list(restored.rglob("payload.age"))
            if len(manifests) != 1 or len(payloads) != 1:
                raise BackupError("snapshot does not contain exactly one artifact")
            public = json.loads(manifests[0].read_text(encoding="utf-8"))
            public = validate_public_manifest(public, int(config["max_artifact_bytes"]))
            if public["host_slug"] != host_slug or public["app_id"] != app_id:
                raise BackupError("snapshot identity mismatch")
            if (public["artifact_id"] != point["artifact_id"]
                    or public["payload_sha256"] != point["payload_sha256"]
                    or public["payload_bytes"] != point["payload_bytes"]):
                raise BackupError("snapshot catalog identity mismatch")
            if sha256(payloads[0]) != public["payload_sha256"]:
                raise BackupError("snapshot payload checksum mismatch")
            archive = root / "payload.tar"
            run(["age", "--decrypt", "--identity", str(identity),
                 "--output", str(archive), str(payloads[0])])
            extracted = root / "extracted"
            extracted.mkdir(mode=0o700)
            safe_extract(archive, extracted, int(config["max_artifact_bytes"]))
            internal = validate_payload(extracted, public)
            manifest_sha256 = sha256(extracted / "payload" / "backup-manifest.json")
            if internal["adapter"] in {"postgres-compose-v1", TRACKER_ADAPTER}:
                adapter_path = restore_adapter_path(config, host_slug, app_id, internal["adapter"])
                result = run([adapter_path, str(extracted / "payload")], timeout=7200)
                try:
                    adapter_value = json.loads(result.stdout)
                except json.JSONDecodeError as exc:
                    raise BackupError("restore adapter did not return valid evidence") from exc
                adapter = (validate_restore_adapter_result(adapter_value)
                           if internal["adapter"] == "postgres-compose-v1"
                           else validate_tracker_restore_adapter_result(adapter_value))
            else:
                raise BackupError("unsupported restore adapter")
        if temporary_path.exists() or temporary_path.is_symlink():
            raise BackupError("restore workspace cleanup did not succeed")
        elapsed_seconds = time.monotonic() - started
        if elapsed_seconds > RESTORE_MAX_DURATION_SECONDS:
            raise BackupError("restore test exceeded the four hour recovery objective")
        duration_seconds = max(1, math.ceil(elapsed_seconds))
        evidence = {
            **public,
            "manifest_sha256": manifest_sha256,
            "snapshot_id": snapshot_id,
            "restore_tested_at": now(),
            "restore_status": "passed",
            "preflight": {
                "key": "passed", "payload_checksum": "passed", "compatibility": "passed",
                "capacity": "passed", "required_bytes": required_bytes, "available_bytes": available_bytes,
            },
            "validation": {
                "schema": "passed", "data": "passed", "application_health": adapter["application_health"],
                "database_content_marker": adapter["database_content_marker"],
                "public_table_count": adapter["public_table_count"],
                "input_files_verified": len(internal["inputs"]),
                "encrypted_two_factor_authentication": adapter["encrypted_secret_validation"],
            },
            "versions": {"adapter": adapter["adapter"], "postgres_image": adapter["postgres_image"],
                         "application_image": adapter["application_image"]},
            "isolation": {"network": adapter["network"],
                          "external_effects_blocked": adapter["external_effects_blocked"],
                          "host_ports": adapter["host_ports"]},
            "cleanup": {"status": "passed", "containers_removed": adapter["containers_removed"],
                        "workspace_removed": True},
            "duration_seconds": duration_seconds,
            "engine_version": ENGINE_VERSION,
        }
        evidence_path = Path(config["evidence_root"]) / host_slug / app_id / f'{public["artifact_id"]}.json'
        atomic_json(evidence_path, evidence)
        return evidence


def write_recovered_secret(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(descriptor, value.encode())
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_recovery_bundle(source: Path) -> tuple[dict[str, str], tuple[int, int]]:
    expected_user = pwd.getpwnam("mtuinstra")
    reject_symlink_components(source)
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise BackupError("independent recovery bundle is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != expected_user.pw_uid
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 128 * 1024):
            raise BackupError("independent recovery bundle is unsafe")
        encoded = b""
        while len(encoded) <= 128 * 1024:
            chunk = os.read(descriptor, min(16 * 1024, 128 * 1024 + 1 - len(encoded)))
            if not chunk:
                break
            encoded += chunk
        if len(encoded) > 128 * 1024:
            raise BackupError("independent recovery bundle is too large")
    finally:
        os.close(descriptor)
    value = json.loads(encoded.decode("utf-8"))
    legacy_keys = {"age_identity", "restic_prod01_umami", "ssh_prod01", "ssh_prod02"}
    keys = legacy_keys | {"ssh_prod01_restore"}
    if (not isinstance(value, dict) or set(value) != {"schema_version", "source_host", "secrets"}
            or value.get("schema_version") != SCHEMA_VERSION or value.get("source_host") != "sanctuary"
            or not isinstance(value.get("secrets"), dict)
            or frozenset(value["secrets"]) not in {frozenset(legacy_keys), frozenset(keys)}
            or any(not isinstance(secret, str) or not 16 <= len(secret) <= 16384
                   for secret in value["secrets"].values())):
        raise BackupError("independent recovery bundle schema is invalid")
    secrets = value["secrets"]
    try:
        secrets["age_identity"].encode("ascii")
    except UnicodeEncodeError as exc:
        raise BackupError("independent recovery bundle secret format is invalid") from exc
    age_secret_lines = [line for line in secrets["age_identity"].splitlines()
                        if line and not line.startswith("#")]
    if (len(age_secret_lines) != 1
            or not re.fullmatch(re.escape(AGE_SECRET_PREFIX) + r"[0-9A-Z]{20,100}", age_secret_lines[0])
            or not secrets["ssh_prod01"].startswith(SSH_PRIVATE_BEGIN)
            or not secrets["ssh_prod02"].startswith(SSH_PRIVATE_BEGIN)
            or not secrets["ssh_prod01"].rstrip().endswith(SSH_PRIVATE_END)
            or not secrets["ssh_prod02"].rstrip().endswith(SSH_PRIVATE_END)
            or ("ssh_prod01_restore" in secrets and (
                not secrets["ssh_prod01_restore"].startswith(SSH_PRIVATE_BEGIN)
                or not secrets["ssh_prod01_restore"].rstrip().endswith(SSH_PRIVATE_END)))):
        raise BackupError("independent recovery bundle secret format is invalid")
    return secrets, (info.st_dev, info.st_ino)


def verify_recovered_transport(host: dict[str, Any], identity: Path, maximum_bytes: int) -> None:
    result = run(["ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes",
                  "-o", f'UserKnownHostsFile={host["known_hosts_file"]}', "-i", str(identity),
                  f'{host["ssh_user"]}@{host["ssh_host"]}', "list"])
    try:
        manifests = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BackupError("independent recovery transport did not return valid evidence") from exc
    if not isinstance(manifests, list) or len(manifests) > 1000:
        raise BackupError("independent recovery transport returned invalid evidence")
    for value in manifests:
        if not isinstance(value, dict):
            raise BackupError("independent recovery transport returned invalid evidence")
        manifest = validate_public_manifest(value, maximum_bytes)
        if (manifest["host_slug"] != host["host_slug"]
                or manifest["app_id"] not in host["applications"]):
            raise BackupError("independent recovery transport identity is invalid")


def escrow_recovery_test(config: dict[str, Any], source: Path = RECOVERY_BUNDLE) -> dict[str, Any]:
    if os.geteuid() != ROOT_UID:
        raise BackupError("independent recovery test requires the privileged restore service")
    secrets, source_identity = validate_recovery_bundle(source)
    restore_work = Path(config["restore_work_dir"])
    ensure_private_runtime_directory(restore_work)
    with tempfile.TemporaryDirectory(prefix="escrow-recovery-", dir=restore_work) as temporary:
        root = Path(temporary)
        password = root / "passwords/tuinstra-prod-01"
        (root / "passwords").mkdir(mode=0o700)
        password.mkdir(mode=0o700)
        identities = root / "ssh"
        identities.mkdir(mode=0o700)
        age_identity = root / "age-identity.txt"
        write_recovered_secret(age_identity, secrets["age_identity"])
        write_recovered_secret(password / "umami.password", secrets["restic_prod01_umami"])
        write_recovered_secret(identities / "prod01", secrets["ssh_prod01"])
        write_recovered_secret(identities / "prod02", secrets["ssh_prod02"])
        if "ssh_prod01_restore" in secrets:
            write_recovered_secret(identities / "prod01-restore", secrets["ssh_prod01_restore"])
        recovered_config = json.loads(json.dumps(config))
        recovered_config["age_identity_file"] = str(age_identity)
        recovered_config["password_root"] = str(root / "passwords")
        hosts = {host["host_slug"]: host for host in recovered_config["hosts"]}
        if set(hosts) != {"tuinstra-prod-01", "tuinstra-prod-02"}:
            raise BackupError("independent recovery host allowlist is invalid")
        maximum_bytes = int(config["max_artifact_bytes"])
        verify_recovered_transport(hosts["tuinstra-prod-01"], identities / "prod01", maximum_bytes)
        verify_recovered_transport(hosts["tuinstra-prod-02"], identities / "prod02", maximum_bytes)
        evidence = restore_test(recovered_config, "tuinstra-prod-01", "umami", "latest")
        # The root-private test is complete and restore_test has atomically persisted
        # success evidence. Only now consume the exact user-readable handoff inode.
        current = source.lstat()
        if (current.st_dev, current.st_ino) != source_identity:
            raise BackupError("independent recovery bundle changed during use")
        source.unlink()
        fsync_directory(source.parent)
    return {"status": "passed", "credential_source": "independent-escrow-copy",
            "host_slug": "tuinstra-prod-01", "app_id": "umami",
            "snapshot_id": evidence["snapshot_id"],
            "ssh_hosts_verified": ["tuinstra-prod-01", "tuinstra-prod-02"]}


def inspect(config: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "generated_at": now(),
        "hosts": []}
    for host in config["hosts"]:
        host_item = {"host_slug": host["host_slug"], "applications": []}
        if not host["applications"]:
            host_item.update({"status": "not-applicable", "reason": "no-enabled-production-applications"})
        for app_id in host["applications"]:
            policy_file = policy_path(config, host["host_slug"], app_id)
            if not policy_file.exists() and not policy_file.is_symlink():
                host_item["applications"].append({"app_id": app_id,
                    "status": "not-applicable", "reason": "no-active-policy"})
                continue
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
    safety_ingest_parser = commands.add_parser("safety-ingest")
    safety_ingest_parser.add_argument("--host", required=True)
    safety_ingest_parser.add_argument("--app", required=True)
    safety_ingest_parser.add_argument("--artifact", required=True)
    safety_ingest_parser.add_argument("--operation", required=True)
    safety_ingest_parser.add_argument("--run-id", required=True)
    safety_ingest_parser.add_argument("--inherited-host-lock-fd", type=int)
    safety_pull_parser = commands.add_parser("safety-pull")
    safety_pull_parser.add_argument("--host", required=True)
    safety_pull_parser.add_argument("--app", required=True)
    safety_pull_parser.add_argument("--artifact", required=True)
    safety_pull_parser.add_argument("--operation", required=True)
    safety_pull_parser.add_argument("--run-id", required=True)
    safety_pull_parser.add_argument("--inherited-host-lock-fd", type=int)
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
    run_active_parser = commands.add_parser("run-active")
    run_active_parser.add_argument("--host", required=True)
    run_active_parser.add_argument("--app", required=True)
    run_active_parser.add_argument("--trigger", choices=sorted(TRIGGERS), required=True)
    ensure_daily_parser = commands.add_parser("ensure-daily")
    ensure_daily_parser.add_argument("--host", required=True)
    ensure_daily_parser.add_argument("--app", required=True)
    ensure_daily_parser.add_argument("--policy-version", required=True)
    ensure_daily_parser.add_argument("--plan-hash", required=True)
    ensure_daily_parser.add_argument("--trigger", choices=sorted(TRIGGERS), required=True)
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
    commands.add_parser("escrow-recovery-test")
    materialize_parser = commands.add_parser("materialize")
    materialize_parser.add_argument("--host", required=True)
    materialize_parser.add_argument("--app", required=True)
    materialize_parser.add_argument("--snapshot", required=True)
    materialize_parser.add_argument("--operation", required=True)
    materialize_parser.add_argument("--purpose", choices=["restore", "rollback"], required=True)
    materialize_parser.add_argument("--inherited-host-lock-fd", type=int)
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
            if os.geteuid() != 0:
                raise BackupError("pull requires the privileged backup cycle service")
            with host_operation_lock(config, args.host):
                result = pull_host(config, args.host)
        elif args.command == "safety-ingest":
            if os.geteuid() != 0:
                raise BackupError("safety ingestion requires the privileged repository service")
            result = safety_ingest(config, args.host, args.app, args.artifact, args.operation, args.run_id,
                                   args.inherited_host_lock_fd)
        elif args.command == "safety-pull":
            if os.geteuid() != 0:
                raise BackupError("safety pull requires the privileged repository service")
            result = safety_pull(config, args.host, args.app, args.artifact, args.operation, args.run_id,
                                 args.inherited_host_lock_fd)
        elif args.command == "retain":
            if os.geteuid() != 0:
                raise BackupError("retention requires the separate privileged service")
            result = retain(config, args.host, args.app, args.policy_version)
        elif args.command == "retain-active":
            if os.geteuid() != 0:
                raise BackupError("retention requires the separate privileged service")
            result = retain_active(config, args.host, args.app)
        elif args.command == "cycle":
            if os.geteuid() != 0:
                raise BackupError("backup cycle requires the privileged cycle service")
            result = cycle(config, args.host, args.app, args.policy_version, args.plan_hash, args.trigger)
        elif args.command == "run-active":
            if os.geteuid() != 0:
                raise BackupError("backup cycle requires the privileged cycle service")
            result = run_active(config, args.host, args.app, args.trigger)
        elif args.command == "ensure-daily":
            if os.geteuid() != 0:
                raise BackupError("backup cycle requires the privileged cycle service")
            result = ensure_daily(config, args.host, args.app, args.policy_version, args.plan_hash, args.trigger)
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
        elif args.command == "escrow-recovery-test":
            result = escrow_recovery_test(config)
        elif args.command == "materialize":
            if os.geteuid() != 0:
                raise BackupError("materialization requires the privileged restore service")
            result = materialize_snapshot(config, args.host, args.app, args.snapshot,
                                          args.operation, args.purpose, args.inherited_host_lock_fd)
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
