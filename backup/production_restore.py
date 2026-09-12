#!/usr/bin/python3
"""Bounded Sanctuary coordinator for a production Umami restore.

The public interface accepts identifiers and digests only. Repository paths,
transport credentials and target commands are loaded from a root-owned profile.
No command retries after a production mutation are performed here; an uncertain
result must be reconciled through ``status``.
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
from typing import Any, BinaryIO, Callable


SAFETY_TAG = "tuinstra:production-restore-safety"
OPERATION_PREFIX = "operation:"
TERMINAL_STATES = {"succeeded", "rolled-back", "resolved"}
MUTATING_STATES = {"preparing", "safety-storing", "applying", "rolling-back"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ARTIFACT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
ROOT_UID = 0


class RestoreError(RuntimeError):
    pass


class TransportUncertain(RestoreError):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_id(value: str, label: str) -> str:
    if not ID_RE.fullmatch(value):
        raise RestoreError(f"invalid {label}")
    return value


def load_config(path: str) -> dict[str, Any]:
    source = Path(path)
    if (source.is_symlink() or not source.is_file() or source.stat().st_uid != ROOT_UID
            or source.stat().st_mode & 0o022):
        raise RestoreError("production restore configuration is unsafe")
    value = json.loads(source.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise RestoreError("unsupported production restore configuration")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(argv: list[str], *, stdin: BinaryIO | None = None, pass_fds: tuple[int, ...] = (),
        timeout: int = 7200) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              pass_fds=pass_fds, timeout=timeout, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RestoreError(f"fixed production restore command failed: {Path(argv[0]).name}") from exc


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    _private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as output:
            json.dump(value, output, sort_keys=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class RestoreRequest:
    def __init__(self, host_slug: str, app_id: str, snapshot_id: str, operation_id: str,
                 plan_hash: str, environment: str = "production"):
        self.host_slug = host_slug
        self.app_id = app_id
        self.snapshot_id = snapshot_id
        self.operation_id = operation_id
        self.plan_hash = plan_hash
        self.environment = environment

    @classmethod
    def create(cls, config: dict[str, Any], host_slug: str, app_id: str, snapshot_id: str,
               operation_id: str, plan_hash: str) -> "RestoreRequest":
        require_id(host_slug, "host slug")
        require_id(app_id, "application id")
        require_id(operation_id, "operation id")
        if not SHA_RE.fullmatch(snapshot_id):
            raise RestoreError("production restore requires a full snapshot id")
        if not SHA_RE.fullmatch(plan_hash):
            raise RestoreError("production restore requires an exact plan hash")
        matches = [item for item in config.get("hosts", [])
                   if item.get("host_slug") == host_slug and app_id in item.get("applications", [])]
        if len(matches) != 1 or (host_slug, app_id) != ("tuinstra-prod-01", "umami"):
            raise RestoreError("production restore target is not allowlisted")
        return cls(host_slug, app_id, snapshot_id, operation_id, plan_hash)

    def binding(self) -> dict[str, str]:
        return {"host_slug": self.host_slug, "app_id": self.app_id,
                "snapshot_id": self.snapshot_id, "operation_id": self.operation_id,
                "plan_hash": self.plan_hash, "environment": self.environment}


def _reject_symlinks(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    for part in (path.parts[1:] if path.is_absolute() else path.parts):
        current /= part
        if current.is_symlink():
            raise RestoreError("controlled production restore path contains a symbolic link")


def _private_directory(path: Path) -> None:
    _reject_symlinks(path)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RestoreError("controlled production restore directory is unsafe")


class JournalStore:
    def __init__(self, root: Path):
        self.root = root

    def path(self, operation_id: str) -> Path:
        require_id(operation_id, "operation id")
        return self.root / f"{operation_id}.json"

    def load(self, operation_id: str) -> dict[str, Any]:
        path = self.path(operation_id)
        if path.is_symlink() or not path.is_file():
            raise RestoreError("production restore journal is unavailable")
        info = path.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise RestoreError("production restore journal has unsafe permissions")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RestoreError("production restore journal is invalid") from exc
        if value.get("schema_version") != 1 or value.get("operation_id") != operation_id:
            raise RestoreError("production restore journal identity is invalid")
        return value

    def create(self, request: RestoreRequest, *, artifact_id: str, manifest_sha256: str,
               stage_sha256: str, restore_evidence_id: str) -> dict[str, Any]:
        if not ARTIFACT_RE.fullmatch(artifact_id):
            raise RestoreError("production restore artifact id is invalid")
        if not SHA_RE.fullmatch(manifest_sha256) or not SHA_RE.fullmatch(stage_sha256):
            raise RestoreError("production restore evidence digest is invalid")
        if not EVIDENCE_RE.fullmatch(restore_evidence_id):
            raise RestoreError("production restore evidence id is invalid")
        value: dict[str, Any] = {
            "schema_version": 1, **request.binding(), "artifact_id": artifact_id,
            "manifest_sha256": manifest_sha256, "stage_sha256": stage_sha256,
            "restore_evidence_id": restore_evidence_id,
            "adapter_version": "umami-production-v1", "state": "preflight-passed",
            "maintenance_active": False, "target_state": None,
            "safety_artifact_id": None, "safety_snapshot_id": None,
            "created_at": now(), "updated_at": now(), "error_code": None,
            "rollback_plan_hash": None,
        }
        path = self.path(request.operation_id)
        _private_directory(self.root)
        if path.exists() or path.is_symlink():
            current = self.load(request.operation_id)
            immutable = set(request.binding()) | {"artifact_id", "manifest_sha256", "stage_sha256",
                                                  "restore_evidence_id", "adapter_version"}
            if any(current.get(key) != value.get(key) for key in immutable):
                raise RestoreError("production restore operation binding changed")
            return current
        atomic_json(path, value)
        return value

    def update(self, operation_id: str, **changes: Any) -> dict[str, Any]:
        forbidden = {"host_slug", "app_id", "snapshot_id", "operation_id", "plan_hash", "environment",
                     "artifact_id", "manifest_sha256", "stage_sha256", "restore_evidence_id",
                     "adapter_version"}
        if forbidden.intersection(changes):
            raise RestoreError("production restore immutable binding cannot change")
        allowed = {"state", "maintenance_active", "target_state", "safety_artifact_id",
                   "safety_snapshot_id", "error_code", "rollback_plan_hash", "updated_at"}
        if not set(changes).issubset(allowed):
            raise RestoreError("production restore journal update is invalid")
        current = self.load(operation_id)
        current.update(changes)
        current["updated_at"] = now()
        atomic_json(self.path(operation_id), current)
        return current


def _binding_matches(journal: dict[str, Any], request: RestoreRequest) -> bool:
    return all(journal.get(key) == value for key, value in request.binding().items())


def _host(config: dict[str, Any], request: RestoreRequest) -> dict[str, Any]:
    matches = [item for item in config["hosts"] if item["host_slug"] == request.host_slug]
    if len(matches) != 1:
        raise RestoreError("production restore host is not allowlisted")
    return matches[0]


@contextlib.contextmanager
def host_operation_lock(config: dict[str, Any], host_slug: str):
    require_id(host_slug, "host slug")
    path = Path(config["host_lock_root"]) / f"operations.host.{host_slug}.lock"
    _reject_symlinks(path.parent)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RestoreError("authoritative host operation lock is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID
                or stat.S_IMODE(info.st_mode) != 0o660):
            raise RestoreError("authoritative host operation lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RestoreError("host has another active operation") from exc
        yield descriptor
    finally:
        os.close(descriptor)


def core_json(config: dict[str, Any], arguments: list[str], lock_descriptor: int | None = None) -> dict[str, Any]:
    argv = [config["executable"], "--config", config["installed_config"], *arguments]
    descriptors: tuple[int, ...] = ()
    if lock_descriptor is not None:
        argv.extend(["--inherited-host-lock-fd", str(lock_descriptor)])
        descriptors = (lock_descriptor,)
    result = run(argv, pass_fds=descriptors)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RestoreError("backup engine returned invalid evidence") from exc
    if not isinstance(value, dict):
        raise RestoreError("backup engine returned an invalid result")
    return value


class RemoteTransport:
    """Dedicated forced-command transport; it never uses the export-only key."""

    def __init__(self, config: dict[str, Any], request: RestoreRequest):
        self.config = config
        self.request = request
        self.host = _host(config, request)

    def _identity(self) -> str:
        credentials = os.environ.get("CREDENTIALS_DIRECTORY")
        if credentials:
            candidate = Path(credentials) / self.host["restore_credential_name"]
        else:
            candidate = Path(self.host["restore_identity_file"])
        _reject_symlinks(candidate.parent)
        if (candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_uid != ROOT_UID
                or candidate.stat().st_mode & 0o077):
            raise RestoreError("production restore transport credential is unavailable")
        return str(candidate)

    def _known_hosts(self) -> str:
        candidate = Path(self.host["known_hosts_file"])
        _reject_symlinks(candidate.parent)
        if (candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_uid != ROOT_UID
                or candidate.stat().st_mode & 0o022):
            raise RestoreError("production restore host identity configuration is unavailable")
        return str(candidate)

    def _argv(self, command: str) -> list[str]:
        return ["/usr/bin/ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={self._known_hosts()}", "-i", self._identity(),
                f'{self.host["restore_ssh_user"]}@{self.host["ssh_host"]}', command]

    def _call(self, command: str, stream: BinaryIO | None = None) -> dict[str, Any]:
        try:
            result = run(self._argv(command), stdin=stream, timeout=7200)
            value = json.loads(result.stdout)
        except (RestoreError, json.JSONDecodeError) as exc:
            raise TransportUncertain("production target result requires reconciliation") from exc
        if not isinstance(value, dict) or not isinstance(value.get("status"), str):
            raise TransportUncertain("production target returned invalid evidence")
        return value

    def stage(self, bundle: Path, purpose: str, plan_hash: str) -> dict[str, Any]:
        if purpose not in {"restore", "rollback"}:
            raise RestoreError("invalid production restore stage purpose")
        digest = sha256(bundle)
        size = bundle.stat().st_size
        with bundle.open("rb") as source:
            return self._call(f"stage {self.request.operation_id} {plan_hash} {purpose} {digest} {size}", source)

    def rpc(self, request: RestoreRequest, action: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if request.binding() != self.request.binding():
            raise RestoreError("transport request binding changed")
        if action not in {"preflight", "prepare", "apply", "status", "rollback"}:
            raise RestoreError("unsupported production restore action")
        parts = [action, request.operation_id, request.plan_hash]
        if action in {"apply", "rollback"}:
            key = "safety_snapshot_id"
            value = (extra or {}).get(key, "")
            if not SHA_RE.fullmatch(value):
                raise RestoreError("recorded safety snapshot id is required")
            parts.append(value)
            if action == "rollback":
                rollback_hash = (extra or {}).get("rollback_plan_hash", "")
                if not SHA_RE.fullmatch(rollback_hash):
                    raise RestoreError("rollback requires an exact plan hash")
                parts.append(rollback_hash)
        return self._call(" ".join(parts))


def _validate_restore_evidence(config: dict[str, Any], request: RestoreRequest,
                               catalog_point: dict[str, Any]) -> tuple[dict[str, Any], str]:
    artifact_id = catalog_point.get("artifact_id", "")
    if not ARTIFACT_RE.fullmatch(artifact_id):
        raise RestoreError("catalog artifact identity is invalid")
    path = Path(config["evidence_root"]) / request.host_slug / request.app_id / f"{artifact_id}.json"
    if path.is_symlink() or not path.is_file():
        raise RestoreError("exact snapshot has no successful isolated restore evidence")
    evidence_info = path.stat()
    if evidence_info.st_uid != ROOT_UID or evidence_info.st_mode & 0o022:
        raise RestoreError("isolated restore evidence has unsafe ownership or permissions")
    evidence = json.loads(path.read_text(encoding="utf-8"))
    preflight = evidence.get("preflight", {})
    validation = evidence.get("validation", {})
    isolation = evidence.get("isolation", {})
    cleanup = evidence.get("cleanup", {})
    if (evidence.get("snapshot_id") != request.snapshot_id or evidence.get("artifact_id") != artifact_id
            or evidence.get("host_slug") != request.host_slug or evidence.get("app_id") != request.app_id
            or evidence.get("restore_status") != "passed" or evidence.get("engine_version") != "tuinstra-backup-v1"
            or not isinstance(evidence.get("duration_seconds"), int) or evidence["duration_seconds"] > 14400
            or not SHA_RE.fullmatch(evidence.get("manifest_sha256", ""))
            or any(preflight.get(key) != "passed" for key in ("key", "payload_checksum", "compatibility", "capacity"))
            or any(validation.get(key) != "passed" for key in (
                "schema", "data", "application_health", "database_content_marker",
                "encrypted_two_factor_authentication",
            ))
            or isolation.get("external_effects_blocked") is not True or isolation.get("host_ports") != 0
            or cleanup.get("status") != "passed" or cleanup.get("containers_removed") is not True
            or cleanup.get("workspace_removed") is not True):
        raise RestoreError("isolated restore evidence does not bind the exact recovery point")
    return evidence, f"restore-{artifact_id}"


def _catalog_point(config: dict[str, Any], request: RestoreRequest) -> dict[str, Any]:
    value = core_json(config, ["catalog", "--host", request.host_slug, "--app", request.app_id])
    matches = [item for item in value["recovery_points"]
               if item.get("snapshot_id") == request.snapshot_id and item.get("state") == "available"]
    if len(matches) != 1 or matches[0].get("integrity_coverage") != "full-repository-data":
        raise RestoreError("exact snapshot is not an available checked recovery point")
    return matches[0]


def materialize_bundle(config: dict[str, Any], request: RestoreRequest, purpose: str,
                       snapshot_id: str | None = None,
                       lock_descriptor: int | None = None) -> tuple[Path, dict[str, Any]]:
    snapshot = snapshot_id or request.snapshot_id
    if not SHA_RE.fullmatch(snapshot):
        raise RestoreError("materialization requires a full snapshot id")
    result = core_json(config, ["materialize", "--host", request.host_slug, "--app", request.app_id,
                                "--snapshot", snapshot, "--operation", request.operation_id,
                                "--purpose", purpose], lock_descriptor)
    expected = {"status": "materialized", "host_slug": request.host_slug, "app_id": request.app_id,
                "snapshot_id": snapshot, "operation_id": request.operation_id, "purpose": purpose}
    if any(result.get(key) != value for key, value in expected.items()):
        raise RestoreError("backup engine materialization binding is invalid")
    if (not ARTIFACT_RE.fullmatch(result.get("artifact_id", ""))
            or not SHA_RE.fullmatch(result.get("manifest_sha256", ""))
            or not SHA_RE.fullmatch(result.get("payload_sha256", ""))
            or result.get("adapter") != "postgres-compose-v1"):
        raise RestoreError("backup engine materialization evidence is invalid")
    materialized = Path(config["materialized_root"]) / request.operation_id / purpose
    _reject_symlinks(materialized)
    info = materialized.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != ROOT_UID or stat.S_IMODE(info.st_mode) != 0o700:
        raise RestoreError("materialized production restore root is unsafe")
    payload = materialized / "payload"
    manifest = payload / "backup-manifest.json"
    if manifest.is_symlink() or not manifest.is_file() or sha256(manifest) != result["manifest_sha256"]:
        raise RestoreError("materialized production restore manifest changed")
    work = Path(config["production_restore_work_root"])
    _private_directory(work)
    operation_root = Path(tempfile.mkdtemp(prefix=f"{request.operation_id}-{purpose}-", dir=work))
    os.chmod(operation_root, 0o700)
    bundle = operation_root / "target.tar"
    with tarfile.open(bundle, "w") as target_archive:
        target_archive.add(payload, arcname="payload", recursive=True)
    os.chmod(bundle, 0o600)
    return bundle, {"result": result, "work_root": str(operation_root)}


def preflight(config: dict[str, Any], request: RestoreRequest, store: JournalStore,
              transport: RemoteTransport) -> dict[str, Any]:
    point = _catalog_point(config, request)
    evidence, evidence_id = _validate_restore_evidence(config, request, point)
    with host_operation_lock(config, request.host_slug) as lock_descriptor:
        bundle, materialized = materialize_bundle(config, request, "restore", lock_descriptor=lock_descriptor)
        try:
            if evidence["manifest_sha256"] != materialized["result"]["manifest_sha256"]:
                raise RestoreError("materialized manifest does not match isolated restore evidence")
            stage_result = transport.stage(bundle, "restore", request.plan_hash)
            if stage_result.get("status") != "staged" or stage_result.get("stage_sha256") != sha256(bundle):
                raise RestoreError("production target did not confirm the exact staged bundle")
            target_result = transport.rpc(request, "preflight")
            if target_result.get("status") != "preflight-passed":
                raise RestoreError("production target preflight did not pass")
            return store.create(request, artifact_id=point["artifact_id"],
                                manifest_sha256=materialized["result"]["manifest_sha256"],
                                stage_sha256=sha256(bundle), restore_evidence_id=evidence_id)
        finally:
            shutil.rmtree(materialized["work_root"], ignore_errors=True)


def _validate_safety_result(result: dict[str, Any], operation_id: str) -> str:
    snapshot_id = result.get("snapshot_id", "")
    tags = result.get("tags", [])
    if (not SHA_RE.fullmatch(snapshot_id)
            or set(tags) != {SAFETY_TAG, f"{OPERATION_PREFIX}{operation_id}"}
            or result.get("integrity_coverage") != "full-repository-data"):
        raise RestoreError("safety backup is not durable, pinned and fully checked")
    return snapshot_id


def apply(config: dict[str, Any], request: RestoreRequest, store: JournalStore,
          transport: RemoteTransport,
          safety_backup: Callable[[RestoreRequest, str, int], dict[str, Any]]) -> dict[str, Any]:
    journal = store.load(request.operation_id)
    if not _binding_matches(journal, request):
        raise RestoreError("production restore operation binding changed")
    if journal["state"] in MUTATING_STATES | {"uncertain"}:
        raise RestoreError("production restore requires status reconcile before any retry")
    if journal["state"] != "preflight-passed":
        raise RestoreError("production restore is not ready to apply")
    try:
        with host_operation_lock(config, request.host_slug) as lock_descriptor:
            store.update(request.operation_id, state="preparing")
            prepared = transport.rpc(request, "prepare")
            if prepared.get("status") != "prepared" or prepared.get("target_state") not in {"empty", "populated"}:
                raise RestoreError("production target did not enter prepared maintenance state")
            safety_artifact = prepared.get("safety_artifact_id")
            store.update(request.operation_id, state="safety-storing", maintenance_active=True,
                         target_state=prepared["target_state"], safety_artifact_id=safety_artifact)
            if prepared["target_state"] == "populated":
                if not isinstance(safety_artifact, str) or not ARTIFACT_RE.fullmatch(safety_artifact):
                    raise RestoreError("populated target did not produce a safety artifact")
                safety_snapshot = _validate_safety_result(safety_backup(request, safety_artifact, lock_descriptor),
                                                          request.operation_id)
            else:
                safety_snapshot = "0" * 64
            store.update(request.operation_id, state="applying", safety_snapshot_id=safety_snapshot)
            try:
                result = transport.rpc(request, "apply", {"safety_snapshot_id": safety_snapshot})
            except TransportUncertain:
                store.update(request.operation_id, state="uncertain", maintenance_active=True,
                             error_code="transport-uncertain")
                raise
            if result.get("status") != "succeeded" or result.get("health") != "passed" or result.get("data") != "passed":
                raise RestoreError("production restore target validation failed")
            return store.update(request.operation_id, state="succeeded", maintenance_active=False,
                                error_code=None)
    except TransportUncertain:
        current = store.load(request.operation_id)
        if current["state"] != "uncertain":
            store.update(request.operation_id, state="uncertain", maintenance_active=True,
                         error_code="transport-uncertain")
        raise
    except Exception:
        current = store.load(request.operation_id)
        store.update(request.operation_id, state="failed",
                     maintenance_active=bool(current.get("maintenance_active")),
                     error_code="restore-failed")
        raise


def status(request: RestoreRequest, store: JournalStore, transport: RemoteTransport) -> dict[str, Any]:
    journal = store.load(request.operation_id)
    if not _binding_matches(journal, request):
        raise RestoreError("production restore operation binding changed")
    remote = transport.rpc(request, "status")
    if journal["state"] == "uncertain" and remote.get("state") in {"succeeded", "failed", "rolled-back"}:
        return store.update(request.operation_id, state=remote["state"],
                            maintenance_active=bool(remote.get("maintenance_active", True)),
                            error_code=None if remote["state"] in {"succeeded", "rolled-back"} else "restore-failed")
    return journal


def rollback(config: dict[str, Any], request: RestoreRequest, rollback_plan_hash: str,
             store: JournalStore, transport: RemoteTransport,
             stage_safety: Callable[[str, str, int], Any]) -> dict[str, Any]:
    if not SHA_RE.fullmatch(rollback_plan_hash):
        raise RestoreError("rollback requires an exact approved plan hash")
    journal = store.load(request.operation_id)
    if not _binding_matches(journal, request) or journal["state"] not in {"failed", "succeeded", "uncertain"}:
        raise RestoreError("production restore is not eligible for rollback")
    snapshot = journal.get("safety_snapshot_id", "")
    if not SHA_RE.fullmatch(snapshot) or snapshot == "0" * 64:
        raise RestoreError("operation has no recorded safety snapshot")
    with host_operation_lock(config, request.host_slug) as lock_descriptor:
        stage_safety(snapshot, "rollback", lock_descriptor)
        store.update(request.operation_id, state="rolling-back", maintenance_active=True,
                     rollback_plan_hash=rollback_plan_hash)
        try:
            result = transport.rpc(request, "rollback", {"safety_snapshot_id": snapshot,
                                                           "rollback_plan_hash": rollback_plan_hash})
        except TransportUncertain:
            store.update(request.operation_id, state="uncertain", maintenance_active=True,
                         error_code="rollback-transport-uncertain")
            raise
        if result.get("status") != "rolled-back" or result.get("health") != "passed" or result.get("data") != "passed":
            store.update(request.operation_id, state="failed", maintenance_active=True,
                         error_code="rollback-failed")
            raise RestoreError("production rollback validation failed")
        return store.update(request.operation_id, state="rolled-back", maintenance_active=False,
                            error_code=None)


def safety_pull(config: dict[str, Any], request: RestoreRequest, artifact_id: str,
                lock_descriptor: int) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    return core_json(config, ["safety-pull", "--host", request.host_slug, "--app", request.app_id,
                              "--artifact", artifact_id, "--operation", request.operation_id,
                              "--run-id", run_id], lock_descriptor)


def stage_rollback(config: dict[str, Any], request: RestoreRequest, rollback_plan_hash: str,
                   transport: RemoteTransport, snapshot_id: str, purpose: str,
                   lock_descriptor: int) -> None:
    if purpose != "rollback":
        raise RestoreError("safety materialization purpose is invalid")
    bundle, materialized = materialize_bundle(config, request, purpose, snapshot_id, lock_descriptor)
    try:
        result = transport.stage(bundle, purpose, rollback_plan_hash)
        if result.get("status") != "staged" or result.get("stage_sha256") != sha256(bundle):
            raise RestoreError("production target did not confirm the rollback bundle")
    finally:
        shutil.rmtree(materialized["work_root"], ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser()
    cli.add_argument("--config", required=True)
    commands = cli.add_subparsers(dest="command", required=True)
    for name in ("preflight", "apply", "status", "rollback"):
        command = commands.add_parser(name)
        command.add_argument("--host", required=True)
        command.add_argument("--app", required=True)
        command.add_argument("--snapshot", required=True)
        command.add_argument("--operation", required=True)
        command.add_argument("--plan-hash", required=True)
        if name == "rollback":
            command.add_argument("--rollback-plan-hash", required=True)
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config)
        request = RestoreRequest.create(config, args.host, args.app, args.snapshot,
                                        args.operation, args.plan_hash)
        store = JournalStore(Path(config["production_restore_journal_root"]))
        transport = RemoteTransport(config, request)
        if args.command == "preflight":
            result = preflight(config, request, store, transport)
        elif args.command == "apply":
            result = apply(config, request, store, transport,
                           lambda exact, artifact, descriptor: safety_pull(config, exact, artifact, descriptor))
        elif args.command == "status":
            result = status(request, store, transport)
        elif args.command == "rollback":
            result = rollback(config, request, args.rollback_plan_hash, store, transport,
                              lambda snapshot, purpose, descriptor: stage_rollback(
                                  config, request, args.rollback_plan_hash, transport,
                                  snapshot, purpose, descriptor))
        else:
            raise RestoreError("unsupported production restore action")
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (RestoreError, KeyError, OSError, ValueError, json.JSONDecodeError):
        print("production restore operation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
