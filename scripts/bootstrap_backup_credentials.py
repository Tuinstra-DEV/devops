#!/usr/bin/env python3
"""Idempotently create Sanctuary-only backup credentials without printing them."""

from __future__ import annotations

import os
import pwd
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


SECRET_ROOT = Path("/etc/tuinstra-backup")
PUBLIC_ROOT = Path("/var/lib/tuinstra-backup/public")
BACKUP_MOUNT = Path("/mnt/hdd1000-01")
MINIMUM_FREE_BYTES = 100 * 1024**3
ROOT_UID = 0
ROOT_GID = 0
SSH_IDENTITY_HOSTS = frozenset({"prod01", "prod02", "prod01-restore"})


class BootstrapError(RuntimeError):
    pass


def require_root() -> None:
    if os.geteuid() != 0:
        raise BootstrapError("run with sudo on Sanctuary")


def validate_mount() -> int:
    if not BACKUP_MOUNT.is_mount():
        raise BootstrapError("approved backup destination is not an active mountpoint")
    available = shutil.disk_usage(BACKUP_MOUNT).free
    if available < MINIMUM_FREE_BYTES:
        raise BootstrapError("approved backup destination has less than 100 GiB free")
    return available


def validate_secret(path: Path, allowed_uids: set[int] | None = None) -> None:
    allowed_uids = {0} if allowed_uids is None else allowed_uids
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid not in allowed_uids or stat.S_IMODE(info.st_mode) != 0o600:
        raise BootstrapError("existing credential has unexpected ownership, type or permissions")


def ssh_credential_uids() -> set[int]:
    allowed = {ROOT_UID}
    try:
        allowed.add(pwd.getpwnam("tuinstra-backup").pw_uid)
    except KeyError:
        pass
    return allowed


def normalize_ssh_identity(path: Path) -> None:
    """Migrate one validated fixed identity from the legacy service owner to root."""
    allowed_paths = {SECRET_ROOT / "ssh" / host for host in SSH_IDENTITY_HOSTS}
    if path not in allowed_paths:
        raise BootstrapError("SSH identity path is not allowlisted")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise BootstrapError("existing SSH identity is unavailable or unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in ssh_credential_uids()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise BootstrapError("existing credential has unexpected ownership, type or permissions")
        os.fchown(descriptor, ROOT_UID, ROOT_GID)
        os.fchmod(descriptor, 0o600)
        normalized = os.fstat(descriptor)
        if (not stat.S_ISREG(normalized.st_mode) or normalized.st_uid != ROOT_UID
                or normalized.st_gid != ROOT_GID or stat.S_IMODE(normalized.st_mode) != 0o600):
            raise BootstrapError("existing SSH identity ownership migration failed")
    finally:
        os.close(descriptor)


def link_secret(temporary: Path, destination: Path) -> None:
    try:
        os.link(temporary, destination)
    except FileExistsError:
        validate_secret(destination)
    finally:
        temporary.unlink(missing_ok=True)
    os.chown(destination, 0, 0)
    os.chmod(destination, 0o600)


def atomic_public(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_public(argv: list[str]) -> bytes:
    try:
        return subprocess.run(argv, check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=30).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError("credential derivation command failed") from exc


def ensure_age_identity() -> Path:
    destination = SECRET_ROOT / "age-identity.txt"
    if not destination.exists():
        descriptor, name = tempfile.mkstemp(prefix=".age-identity.", dir=SECRET_ROOT)
        os.close(descriptor)
        temporary = Path(name)
        temporary.unlink()
        try:
            subprocess.run(["age-keygen", "-o", str(temporary)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            link_secret(temporary, destination)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            temporary.unlink(missing_ok=True)
            raise BootstrapError("age identity generation failed") from exc
    validate_secret(destination)
    atomic_public(PUBLIC_ROOT / "age-recipient.txt", run_public(["age-keygen", "-y", str(destination)]))
    return destination


def ensure_ssh_identity(host: str) -> Path:
    if host not in SSH_IDENTITY_HOSTS:
        raise BootstrapError("SSH identity name is not allowlisted")
    destination = SECRET_ROOT / "ssh" / host
    if not destination.exists():
        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{host}.", dir=SECRET_ROOT / "ssh"))
        temporary = temporary_dir / "identity"
        try:
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                            "-C", f"tuinstra-backup-{host}", "-f", str(temporary)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            link_secret(temporary, destination)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise BootstrapError("SSH identity generation failed") from exc
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)
    normalize_ssh_identity(destination)
    public = run_public(["ssh-keygen", "-y", "-f", str(destination)])
    atomic_public(PUBLIC_ROOT / f"{host}.pub", public.rstrip(b"\n") + f" tuinstra-backup-{host}\n".encode())
    return destination


def require_distinct_ssh_identities(identities: list[Path]) -> None:
    public_keys = {
        run_public(["ssh-keygen", "-y", "-f", str(identity)]).strip()
        for identity in identities
    }
    if len(public_keys) != len(identities) or b"" in public_keys:
        raise BootstrapError("backup and production restore SSH identities must be distinct")


def ensure_restic_password(host_slug: str, app_id: str) -> Path:
    allowed = {
        ("tuinstra-prod-01", "umami"),
        ("tuinstra-prod-01", "status"),
        ("tuinstra-prod-02", "tracker"),
    }
    if (host_slug, app_id) not in allowed:
        raise BootstrapError("Restic credential target is not allowlisted")
    destination = SECRET_ROOT / "restic-passwords" / host_slug / f"{app_id}.password"
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        validate_secret(destination)
    else:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(32) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(destination, 0, 0)
    validate_secret(destination)
    return destination


def main() -> int:
    try:
        require_root()
        for command in ("age-keygen", "ssh-keygen"):
            if shutil.which(command) is None:
                raise BootstrapError(f"required tool is unavailable: {command}")
        available = validate_mount()
        for path, mode in ((SECRET_ROOT, 0o700), (SECRET_ROOT / "ssh", 0o700),
                           (SECRET_ROOT / "restic-passwords", 0o700),
                           (SECRET_ROOT / "restic-passwords" / "tuinstra-prod-01", 0o700),
                           (SECRET_ROOT / "restic-passwords" / "tuinstra-prod-02", 0o700),
                           (PUBLIC_ROOT, 0o755)):
            path.mkdir(parents=True, exist_ok=True)
            os.chown(path, 0, 0)
            os.chmod(path, mode)
        ensure_age_identity()
        identities = [
            ensure_ssh_identity("prod01"),
            ensure_ssh_identity("prod02"),
            ensure_ssh_identity("prod01-restore"),
        ]
        require_distinct_ssh_identities(identities)
        ensure_restic_password("tuinstra-prod-01", "umami")
        ensure_restic_password("tuinstra-prod-01", "status")
        ensure_restic_password("tuinstra-prod-02", "tracker")
        print(f"backup credential bootstrap complete; destination_free_gib={available // 1024**3}; public_dir={PUBLIC_ROOT}")
        return 0
    except (BootstrapError, OSError) as exc:
        message = str(exc) if isinstance(exc, BootstrapError) else "filesystem operation failed"
        print(f"backup credential bootstrap failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
