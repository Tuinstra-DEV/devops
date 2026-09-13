#!/usr/bin/python3
"""Root-owned, forced-command production restore target for Umami only."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable


ROOT_UID = 0
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ARTIFACT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
APPROVED_IMAGES = {
    "docker.io/library/postgres:15-alpine@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b",
    "ghcr.io/umami-software/umami:3.3.1@sha256:fa32d116cf20cad52cbc3fad9a63b46e7fa02299d8f967168eb453d49c476b4a",
}
POSTGRES_IMAGE = "docker.io/library/postgres:15-alpine@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b"
REQUIRED_FILES = {
    "compose", "postgres-env", "umami-env", "database-password", "app-secret",
    "two-factor-encryption-key", "admin-password",
}


class TargetError(RuntimeError):
    pass


class TargetConfig:
    def __init__(self, *, stage_root: Path, journal_root: Path, app_root: Path, secret_root: Path,
                 data_root: Path, caddy_route: Path, caddy_compose: Path, producer_engine: Path,
                 producer_config: Path, host_lock: Path, maintenance_root: Path,
                 maximum_stage_bytes: int, docker_bin: Path = Path("/usr/bin/docker"),
                 compose_project: str = "umami", postgres_uid: int = 70, postgres_gid: int = 70):
        self.stage_root = stage_root
        self.journal_root = journal_root
        self.app_root = app_root
        self.secret_root = secret_root
        self.data_root = data_root
        self.caddy_route = caddy_route
        self.caddy_compose = caddy_compose
        self.producer_engine = producer_engine
        self.producer_config = producer_config
        self.host_lock = host_lock
        self.maintenance_root = maintenance_root
        self.maximum_stage_bytes = maximum_stage_bytes
        self.docker_bin = docker_bin
        self.compose_project = _require_id(compose_project, "Compose project")
        self.postgres_uid = postgres_uid
        self.postgres_gid = postgres_gid

    @classmethod
    def production(cls) -> "TargetConfig":
        return cls(
            stage_root=Path("/var/lib/tuinstra-production-restore/staging"),
            journal_root=Path("/var/lib/tuinstra-production-restore/journal"),
            app_root=Path("/var/www/umami"), secret_root=Path("/etc/tuinstra/umami"),
            data_root=Path("/var/lib/tuinstra/umami"),
            caddy_route=Path("/var/www/_platform/caddy/sites/umami.caddy"),
            caddy_compose=Path("/var/www/_platform/caddy/compose.yml"),
            producer_engine=Path("/usr/local/libexec/tuinstra-backup/tuinstra_backup.py"),
            producer_config=Path("/etc/tuinstra-backup/producer.json"),
            host_lock=Path("/run/lock/tuinstra/operations.host.tuinstra-prod-01.lock"),
            maintenance_root=Path("/var/lib/tuinstra-production-restore/maintenance"),
            maximum_stage_bytes=10 * 1024 * 1024 * 1024,
        )

    @classmethod
    def for_test(cls, root: Path) -> "TargetConfig":
        lock = root / "operations.host.tuinstra-prod-01.lock"
        lock.touch()
        lock.chmod(0o660)
        route = root / "caddy/sites/umami.caddy"
        route.parent.mkdir(parents=True)
        route.write_text("umami.example { reverse_proxy umami:3000 }\n")
        route.chmod(0o644)
        compose = root / "caddy/compose.yml"
        compose.write_text("services: {}\n")
        for path in (root / "app", root / "secrets", root / "data/postgres"):
            path.mkdir(parents=True, exist_ok=True)
        return cls(stage_root=root / "staging", journal_root=root / "journal", app_root=root / "app",
                   secret_root=root / "secrets", data_root=root / "data", caddy_route=route,
                   caddy_compose=compose, producer_engine=root / "engine", producer_config=root / "producer.json",
                   host_lock=lock, maintenance_root=root / "maintenance", maximum_stage_bytes=1024 * 1024,
                   postgres_uid=os.getuid(), postgres_gid=os.getgid())


def _require_id(value: str, label: str) -> str:
    if not ID_RE.fullmatch(value):
        raise TargetError(f"invalid {label}")
    return value


def _require_sha(value: str, label: str) -> str:
    if not SHA_RE.fullmatch(value):
        raise TargetError(f"invalid {label}")
    return value


def _reject_symlinks(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path.cwd()
    for part in (path.parts[1:] if path.is_absolute() else path.parts):
        current /= part
        if current.is_symlink():
            raise TargetError("controlled path contains a symbolic link")


def _private_directory(path: Path) -> None:
    _reject_symlinks(path)
    path.mkdir(parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise TargetError("controlled directory is unavailable")
    os.chmod(path, 0o700)
    info = path.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise TargetError("controlled directory has unsafe ownership or permissions")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(argv: list[str], *, stdin: BinaryIO | None = None, timeout: int = 7200) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout, check=True)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise TargetError(f"fixed restore command failed: {Path(argv[0]).name}") from exc


@contextlib.contextmanager
def host_lock(config: TargetConfig):
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(config.host_lock, flags)
    except OSError as exc:
        raise TargetError("authoritative host operation lock is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID
                or stat.S_IMODE(info.st_mode) != 0o660):
            raise TargetError("authoritative host operation lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise TargetError("host has another active operation") from exc
        yield
    finally:
        os.close(descriptor)


class TargetJournalStore:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, operation_id: str) -> Path:
        return self.root / f"{_require_id(operation_id, 'operation id')}.json"

    def load(self, operation_id: str) -> dict[str, Any]:
        path = self._path(operation_id)
        if path.is_symlink() or not path.is_file():
            raise TargetError("target operation journal is unavailable")
        info = path.stat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise TargetError("target operation journal has unsafe permissions")
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("operation_id") != operation_id or value.get("schema_version") != 1:
            raise TargetError("target operation journal identity is invalid")
        return value

    def create(self, operation_id: str, plan_hash: str, purpose: str, stage_sha256: str) -> dict[str, Any]:
        if purpose not in {"restore", "rollback"}:
            raise TargetError("invalid stage purpose")
        _require_sha(plan_hash, "plan hash")
        _require_sha(stage_sha256, "stage checksum")
        value = {"schema_version": 1, "operation_id": _require_id(operation_id, "operation id"),
                 "plan_hash": plan_hash, "purpose": purpose, "stage_sha256": stage_sha256,
                 "state": "staged", "maintenance_active": False, "target_state": None,
                 "safety_artifact_id": None, "safety_snapshot_id": None, "error_code": None,
                 "rollback_plan_hash": None, "rollback_stage_sha256": None}
        path = self._path(operation_id)
        if path.exists() or path.is_symlink():
            current = self.load(operation_id)
            for key in ("operation_id", "plan_hash", "purpose", "stage_sha256"):
                if current.get(key) != value[key]:
                    raise TargetError("target operation binding changed")
            return current
        _atomic_json(path, value)
        return value

    def update(self, operation_id: str, **changes: Any) -> dict[str, Any]:
        immutable = {"operation_id", "plan_hash", "purpose", "stage_sha256"}
        if immutable.intersection(changes):
            raise TargetError("target immutable operation binding cannot change")
        allowed = {"state", "maintenance_active", "target_state", "safety_artifact_id",
                   "safety_snapshot_id", "error_code", "rollback_plan_hash", "rollback_stage_sha256"}
        if not set(changes).issubset(allowed):
            raise TargetError("target journal update is invalid")
        value = self.load(operation_id)
        value.update(changes)
        _atomic_json(self._path(operation_id), value)
        return value


def _stage_path(config: TargetConfig, operation_id: str, purpose: str) -> Path:
    _require_id(operation_id, "operation id")
    if purpose not in {"restore", "rollback"}:
        raise TargetError("invalid stage purpose")
    return config.stage_root / operation_id / purpose


def _validate_payload(payload: Path) -> dict[str, Any]:
    manifest_path = payload / "backup-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise TargetError("staged restore manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TargetError("staged restore manifest is invalid") from exc
    if (manifest.get("schema_version") != 1 or manifest.get("host_slug") != "tuinstra-prod-01"
            or manifest.get("app_id") != "umami" or manifest.get("adapter") != "postgres-compose-v1"
            or set(manifest.get("images", [])) != APPROVED_IMAGES
            or not ARTIFACT_RE.fullmatch(manifest.get("artifact_id", ""))):
        raise TargetError("staged restore identity or image set is not approved")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        raise TargetError("staged restore inputs are invalid")
    names = set()
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"name", "sha256", "bytes"}:
            raise TargetError("staged restore input metadata is invalid")
        relative = Path(str(item["name"]))
        if relative.is_absolute() or ".." in relative.parts or str(relative) in names:
            raise TargetError("staged restore input path is unsafe")
        source = payload / relative
        if (source.is_symlink() or not source.is_file() or source.stat().st_uid != os.geteuid()
                or not SHA_RE.fullmatch(str(item["sha256"]))
                or _sha256(source) != item["sha256"] or source.stat().st_size != item["bytes"]):
            raise TargetError("staged restore input failed integrity validation")
        names.add(str(relative))
    expected = {"database.dump"} | {f"files/{name}" for name in REQUIRED_FILES}
    if names != expected:
        raise TargetError("staged restore input set is incomplete or unexpected")
    actual = {path.relative_to(payload).as_posix() for path in payload.rglob("*") if path.is_file()}
    if actual != expected | {"backup-manifest.json"}:
        raise TargetError("staged restore contains untracked files")
    return manifest


def stage(config: TargetConfig, operation_id: str, plan_hash: str, purpose: str, digest: str,
          size: int, source: BinaryIO) -> dict[str, Any]:
    _require_id(operation_id, "operation id")
    _require_sha(plan_hash, "plan hash")
    _require_sha(digest, "stage checksum")
    if purpose not in {"restore", "rollback"} or not 0 < size <= config.maximum_stage_bytes:
        raise TargetError("staged restore size or purpose is outside policy")
    root = _stage_path(config, operation_id, purpose)
    _private_directory(config.stage_root)
    _private_directory(root.parent)
    if shutil.disk_usage(config.stage_root).free < size * 2:
        raise TargetError("production restore staging has insufficient free space")
    if root.exists() or root.is_symlink():
        raise TargetError("staged restore already exists")
    temporary = Path(tempfile.mkdtemp(prefix=f".{purpose}-", dir=root.parent))
    os.chmod(temporary, 0o700)
    archive = temporary / "bundle.tar"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(archive, flags, 0o600)
        copied = 0
        try:
            while copied <= size:
                chunk = source.read(min(1024 * 1024, size + 1 - copied))
                if not chunk:
                    break
                copied += len(chunk)
                if copied > size:
                    raise TargetError("staged restore stream exceeds declared size")
                os.write(descriptor, chunk)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if copied != size or _sha256(archive) != digest:
            raise TargetError("staged restore checksum or size mismatch")
        extracted = temporary / "extracted"
        extracted.mkdir(mode=0o700)
        with tarfile.open(archive, "r") as bundle:
            members = bundle.getmembers()
            if len(members) > 32 or sum(member.size for member in members) > config.maximum_stage_bytes:
                raise TargetError("staged restore archive exceeds extraction policy")
            for member in members:
                relative = Path(member.name)
                if (relative.is_absolute() or ".." in relative.parts or member.issym()
                        or not relative.parts or relative.parts[0] != "payload"
                        or member.islnk() or not (member.isfile() or member.isdir())):
                    raise TargetError("unsafe staged restore archive member")
            bundle.extractall(extracted, filter="data")
        payload = extracted / "payload"
        for extracted_file in (path for path in payload.rglob("*") if path.is_file()):
            os.chmod(extracted_file, 0o600)
        _validate_payload(payload)
        archive.unlink()
        os.replace(temporary, root)
        store = TargetJournalStore(config.journal_root)
        if purpose == "restore":
            store.create(operation_id, plan_hash, purpose, digest)
        else:
            journal = store.load(operation_id)
            if journal["state"] not in {"failed", "succeeded"}:
                raise TargetError("target operation is not eligible for rollback staging")
            store.update(operation_id, rollback_plan_hash=plan_hash, rollback_stage_sha256=digest)
        return {"status": "staged", "operation_id": operation_id, "purpose": purpose,
                "stage_sha256": digest}
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


class UmamiAdapter:
    def __init__(self, config: TargetConfig, runner: Callable[..., subprocess.CompletedProcess[bytes]] = _run):
        self.config = config
        self.runner = runner

    def _compose(self, *args: str) -> list[str]:
        return [str(self.config.docker_bin), "compose", "--project-name", self.config.compose_project, "--project-directory",
                str(self.config.app_root), "--file", str(self.config.app_root / "compose.yml"), *args]

    def _operation_root(self, operation_id: str) -> Path:
        path = self.config.journal_root / f"{operation_id}.files"
        _private_directory(path)
        return path

    def target_state(self) -> str:
        postgres = self.config.data_root / "postgres"
        if postgres.is_symlink():
            raise TargetError("PostgreSQL data target is unsafe")
        return "populated" if postgres.is_dir() and any(postgres.iterdir()) else "empty"

    def validate_restore(self, payload: Path) -> None:
        _validate_payload(payload)
        required = max((payload / "database.dump").stat().st_size * 3, 1024 * 1024 * 1024)
        if shutil.disk_usage(self.config.data_root).free < required:
            raise TargetError("production restore target has insufficient free space")
        with (payload / "database.dump").open("rb") as dump:
            self.runner([str(self.config.docker_bin), "run", "--rm", "--interactive", "--network", "none",
                         "--entrypoint", "pg_restore", POSTGRES_IMAGE, "--list"], stdin=dump)

    def enable_maintenance(self, operation_id: str) -> None:
        route = self.config.caddy_route
        if route.is_symlink() or not route.is_file() or route.stat().st_uid != ROOT_UID:
            raise TargetError("Umami Caddy route is not root controlled")
        operation = self._operation_root(operation_id)
        _private_directory(self.config.maintenance_root)
        marker = self.config.maintenance_root / "umami"
        if marker.is_symlink() or marker.exists():
            raise TargetError("another maintenance operation is active")
        marker.write_text(f"{operation_id}\n", encoding="utf-8")
        os.chmod(marker, 0o600)
        saved = operation / "umami.caddy.original"
        if not saved.exists():
            shutil.copyfile(route, saved, follow_symlinks=False)
            os.chmod(saved, 0o600)

    def publish_maintenance_route(self, operation_id: str) -> None:
        marker = self.config.maintenance_root / "umami"
        if marker.is_symlink() or not marker.is_file() or marker.read_text(encoding="utf-8") != f"{operation_id}\n":
            raise TargetError("maintenance marker does not bind this operation")
        route = self.config.caddy_route
        temporary = route.with_name(f".{route.name}.{operation_id}.tmp")
        temporary.write_text('umami.tuinstra.dev {\n\trespond "Service temporarily unavailable" 503\n}\n',
                             encoding="utf-8")
        os.chmod(temporary, 0o644)
        os.replace(temporary, route)
        self._reload_caddy()

    def disable_maintenance(self, operation_id: str) -> None:
        saved = self._operation_root(operation_id) / "umami.caddy.original"
        if saved.is_symlink() or not saved.is_file():
            raise TargetError("original Umami Caddy route is unavailable")
        temporary = self.config.caddy_route.with_name(f".{self.config.caddy_route.name}.{operation_id}.tmp")
        shutil.copyfile(saved, temporary, follow_symlinks=False)
        os.chmod(temporary, 0o644)
        os.replace(temporary, self.config.caddy_route)
        self._reload_caddy()
        marker = self.config.maintenance_root / "umami"
        if marker.is_symlink() or not marker.is_file():
            raise TargetError("maintenance marker is unavailable")
        if marker.read_text(encoding="utf-8") != f"{operation_id}\n":
            raise TargetError("maintenance marker belongs to another operation")
        marker.unlink()

    def _reload_caddy(self) -> None:
        base = [str(self.config.docker_bin), "compose", "--project-directory", str(self.config.caddy_compose.parent),
                "--file", str(self.config.caddy_compose), "exec", "-T", "caddy", "caddy"]
        self.runner(base + ["validate", "--config", "/etc/caddy/Caddyfile"])
        self.runner(base + ["reload", "--config", "/etc/caddy/Caddyfile"])

    def quiesce_writes(self) -> None:
        self.runner(self._compose("stop", "umami"))

    def create_safety_export(self) -> str:
        result = self.runner([str(self.config.producer_engine), "--config", str(self.config.producer_config),
                              "export", "--app", "umami"])
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise TargetError("safety export did not return valid evidence") from exc
        artifact = value.get("artifact_id", "")
        if not ARTIFACT_RE.fullmatch(artifact):
            raise TargetError("safety export did not return an artifact identity")
        return artifact

    def _install_file(self, source: Path, destination: Path, mode: int) -> None:
        if source.is_symlink() or not source.is_file():
            raise TargetError("restore source file is unsafe")
        _reject_symlinks(destination.parent)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, mode)
        try:
            with source.open("rb") as handle, os.fdopen(descriptor, "wb", closefd=False) as output:
                shutil.copyfileobj(handle, output)
                output.flush()
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)

    def _record_restored_admin_state(self, operation_id: str) -> None:
        marker = self.config.secret_root / "admin-bootstrap.complete"
        _reject_symlinks(marker.parent)
        temporary = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        try:
            os.write(descriptor, f"restored:{operation_id}\n".encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, marker)
        directory = os.open(marker.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _restore_database(self, payload: Path, operation_id: str) -> Path:
        work = self.config.data_root / f".restore-{operation_id}"
        if work.exists() or work.is_symlink():
            raise TargetError("database restore workspace already exists")
        work.mkdir(mode=0o700)
        os.chown(work, self.config.postgres_uid, self.config.postgres_gid)
        container = f"tuinstra-restore-{operation_id}-db"
        self.runner([str(self.config.docker_bin), "run", "--detach", "--name", container, "--network", "none",
                     "--env-file", str(payload / "files/postgres-env"),
                     "--mount", f"type=bind,source={work},target=/var/lib/postgresql/data",
                     "--security-opt", "no-new-privileges:true", POSTGRES_IMAGE])
        try:
            for _ in range(60):
                readiness = subprocess.run([str(self.config.docker_bin), "exec", container, "sh", "-eu", "-c",
                                            'psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" '
                                            '--command="select 1"'],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if readiness.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise TargetError("replacement PostgreSQL did not become ready")
            with (payload / "database.dump").open("rb") as dump:
                self.runner([str(self.config.docker_bin), "exec", "-i", container, "sh", "-eu", "-c",
                             'exec pg_restore --exit-on-error --no-owner --username="$POSTGRES_USER" '
                             '--dbname="$POSTGRES_DB"'], stdin=dump)
            result = self.runner([str(self.config.docker_bin), "exec", container, "sh", "-eu", "-c",
                                  'psql --tuples-only --no-align --username="$POSTGRES_USER" '
                                  '--dbname="$POSTGRES_DB" --command="select count(*) from '
                                  'information_schema.tables where table_schema = \'public\'"'])
            if not re.fullmatch(rb"[1-9][0-9]*\s*", result.stdout):
                raise TargetError("replacement database has no application tables")
        finally:
            subprocess.run([str(self.config.docker_bin), "rm", "--force", container], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        return work

    def apply(self, payload: Path, operation_id: str) -> dict[str, str]:
        _validate_payload(payload)
        had_current_data = self.target_state() == "populated"
        replacement = self._restore_database(payload, operation_id)
        if had_current_data:
            self.runner(self._compose("stop", "db"))
        self._install_file(payload / "files/compose", self.config.app_root / "compose.yml", 0o640)
        destinations = {"postgres-env": "postgres.env", "umami-env": "umami.env",
                        "database-password": "database-password", "app-secret": "app-secret",
                        "two-factor-encryption-key": "two-factor-encryption-key",
                        "admin-password": "admin-password"}
        for source_name, destination_name in destinations.items():
            self._install_file(payload / "files" / source_name,
                               self.config.secret_root / destination_name, 0o600)
        current = self.config.data_root / "postgres"
        previous = self.config.data_root / f"postgres.pre-{operation_id}"
        if previous.exists() or previous.is_symlink():
            raise TargetError("previous database preservation path already exists")
        if current.exists():
            os.replace(current, previous)
        os.replace(replacement, current)
        if previous.is_dir() and not previous.is_symlink():
            shutil.rmtree(previous)
        try:
            self.runner(self._compose("up", "--detach", "--remove-orphans"))
            health = None
            for _ in range(90):
                try:
                    health = self.runner(self._compose("exec", "-T", "umami", "curl", "--fail", "--silent",
                                                           "http://127.0.0.1:3000/api/heartbeat"))
                    break
                except TargetError:
                    time.sleep(1)
            if health is None:
                raise TargetError("restored Umami did not become healthy")
            data = self.runner(self._compose("exec", "-T", "db", "sh", "-eu", "-c",
                                                 'psql --tuples-only --no-align --username="$POSTGRES_USER" '
                                                 '--dbname="$POSTGRES_DB" --command="select count(*) from '
                                                 'information_schema.tables where table_schema = \'public\'"'))
            if health.returncode != 0 or not re.fullmatch(rb"[1-9][0-9]*\s*", data.stdout):
                raise TargetError("restored Umami health or data validation failed")
            self._record_restored_admin_state(operation_id)
        except Exception:
            raise
        return {"health": "passed", "data": "passed"}


def _payload(config: TargetConfig, operation_id: str, purpose: str) -> Path:
    payload = _stage_path(config, operation_id, purpose) / "extracted/payload"
    _validate_payload(payload)
    return payload


def preflight(config: TargetConfig, operation_id: str, plan_hash: str,
              adapter: UmamiAdapter) -> dict[str, Any]:
    journal = TargetJournalStore(config.journal_root).load(operation_id)
    if journal["plan_hash"] != plan_hash or journal["purpose"] != "restore" or journal["state"] != "staged":
        raise TargetError("target preflight binding or state is invalid")
    _validate_payload(_payload(config, operation_id, "restore"))
    adapter.validate_restore(_payload(config, operation_id, "restore"))
    state = adapter.target_state()
    return {"status": "preflight-passed", "operation_id": operation_id, "target_state": state}


def prepare(config: TargetConfig, operation_id: str, plan_hash: str,
            adapter: UmamiAdapter) -> dict[str, Any]:
    store = TargetJournalStore(config.journal_root)
    journal = store.load(operation_id)
    if journal["plan_hash"] != plan_hash or journal["purpose"] != "restore" or journal["state"] != "staged":
        raise TargetError("target prepare binding or state is invalid")
    state = adapter.target_state()
    try:
        adapter.enable_maintenance(operation_id)
        adapter.publish_maintenance_route(operation_id)
        if state == "populated":
            adapter.quiesce_writes()
        safety = adapter.create_safety_export() if state == "populated" else None
        store.update(operation_id, state="prepared", maintenance_active=True,
                     target_state=state, safety_artifact_id=safety)
        return {"status": "prepared", "target_state": state, "safety_artifact_id": safety}
    except Exception:
        marker = config.maintenance_root / "umami"
        marker_active = (marker.is_file() and not marker.is_symlink()
                         and marker.read_text(encoding="utf-8") == f"{operation_id}\n")
        store.update(operation_id, state="failed", maintenance_active=marker_active,
                     target_state=state, error_code="prepare-failed")
        raise


def apply(config: TargetConfig, operation_id: str, plan_hash: str, safety_snapshot_id: str,
          adapter: UmamiAdapter) -> dict[str, Any]:
    _require_sha(safety_snapshot_id, "safety snapshot id")
    store = TargetJournalStore(config.journal_root)
    journal = store.load(operation_id)
    if (journal["plan_hash"] != plan_hash or journal["purpose"] != "restore"
            or journal["state"] != "prepared" or not journal["maintenance_active"]):
        raise TargetError("target apply binding or state is invalid")
    if journal["target_state"] == "populated" and safety_snapshot_id == "0" * 64:
        raise TargetError("populated target requires a durable safety snapshot")
    store.update(operation_id, state="applying", safety_snapshot_id=safety_snapshot_id)
    try:
        evidence = adapter.apply(_payload(config, operation_id, "restore"), operation_id)
        adapter.disable_maintenance(operation_id)
        store.update(operation_id, state="succeeded", maintenance_active=False, error_code=None)
        return {"status": "succeeded", **evidence}
    except Exception:
        store.update(operation_id, state="failed", maintenance_active=True, error_code="apply-failed")
        raise


def rollback(config: TargetConfig, operation_id: str, original_plan_hash: str,
             safety_snapshot_id: str, rollback_plan_hash: str, adapter: UmamiAdapter) -> dict[str, Any]:
    _require_sha(safety_snapshot_id, "safety snapshot id")
    _require_sha(rollback_plan_hash, "rollback plan hash")
    store = TargetJournalStore(config.journal_root)
    journal = store.load(operation_id)
    if (journal["plan_hash"] != original_plan_hash or journal.get("safety_snapshot_id") != safety_snapshot_id
            or journal["state"] not in {"failed", "succeeded"}
            or journal.get("rollback_plan_hash") != rollback_plan_hash
            or not SHA_RE.fullmatch(journal.get("rollback_stage_sha256") or "")):
        raise TargetError("target rollback is not bound to the recorded safety snapshot")
    staged = _stage_path(config, operation_id, "rollback") / "extracted/payload"
    _validate_payload(staged)
    store.update(operation_id, state="rolling-back", maintenance_active=True,
                 rollback_plan_hash=rollback_plan_hash)
    try:
        evidence = adapter.apply(staged, f"{operation_id}-rollback")
        adapter.disable_maintenance(operation_id)
        store.update(operation_id, state="rolled-back", maintenance_active=False, error_code=None)
        return {"status": "rolled-back", **evidence}
    except Exception:
        store.update(operation_id, state="failed", maintenance_active=True, error_code="rollback-failed")
        raise


def dispatch(config: TargetConfig, original: str, source: BinaryIO, adapter: UmamiAdapter) -> dict[str, Any]:
    parts = original.split()
    if len(parts) == 6 and parts[0] == "stage":
        try:
            size = int(parts[5])
        except ValueError as exc:
            raise TargetError("invalid staged restore size") from exc
        return stage(config, parts[1], parts[2], parts[3], parts[4], size, source)
    if len(parts) == 3 and parts[0] in {"preflight", "prepare", "status"}:
        operation_id, plan_hash = parts[1], parts[2]
        if parts[0] == "preflight":
            with host_lock(config):
                return preflight(config, operation_id, plan_hash, adapter)
        if parts[0] == "prepare":
            with host_lock(config):
                return prepare(config, operation_id, plan_hash, adapter)
        journal = TargetJournalStore(config.journal_root).load(operation_id)
        if journal["plan_hash"] != plan_hash:
            raise TargetError("target status binding is invalid")
        return {"status": "status", "operation_id": operation_id, "state": journal["state"],
                "maintenance_active": journal["maintenance_active"]}
    if len(parts) == 4 and parts[0] == "apply":
        with host_lock(config):
            return apply(config, parts[1], parts[2], parts[3], adapter)
    if len(parts) == 5 and parts[0] == "rollback":
        with host_lock(config):
            return rollback(config, parts[1], parts[2], parts[3], parts[4], adapter)
    raise TargetError("unsupported production restore target command")


def main() -> int:
    try:
        if os.geteuid() != 0:
            raise TargetError("production restore target requires its fixed privileged dispatcher")
        result = dispatch(TargetConfig.production(), os.environ.get("SSH_ORIGINAL_COMMAND", ""),
                          sys.stdin.buffer, UmamiAdapter(TargetConfig.production()))
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (TargetError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, tarfile.TarError):
        print("production restore target operation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
