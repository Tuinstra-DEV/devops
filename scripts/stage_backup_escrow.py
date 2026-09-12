#!/usr/bin/env python3
"""Create one fixed, one-time user-readable escrow handoff without logging secrets."""

from __future__ import annotations

import json
import os
import pwd
import stat
import sys
from pathlib import Path


ROOT_UID = 0
SECRET_ROOT = Path("/etc/tuinstra-backup")
DESTINATION = Path("/home/mtuinstra/.local/share/tuinstra-backup-escrow.json")
MARKER = Path("/var/lib/tuinstra-backup/escrow-staged-v1")
SOURCES = {
    "age_identity": SECRET_ROOT / "age-identity.txt",
    "restic_prod01_umami": SECRET_ROOT / "restic-passwords/tuinstra-prod-01/umami.password",
    "ssh_prod01": SECRET_ROOT / "ssh/prod01",
    "ssh_prod02": SECRET_ROOT / "ssh/prod02",
}
MAX_SECRET_BYTES = 16 * 1024


class StageError(RuntimeError):
    pass


def read_secret(path: Path) -> str:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise StageError("a fixed backup credential is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID
                or stat.S_IMODE(info.st_mode) != 0o600 or not 0 < info.st_size <= MAX_SECRET_BYTES):
            raise StageError("a fixed backup credential has unsafe metadata")
        value = os.read(descriptor, MAX_SECRET_BYTES + 1)
        if len(value) != info.st_size:
            raise StageError("a fixed backup credential changed while reading")
        return value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise StageError("a fixed backup credential has invalid encoding") from exc
    finally:
        os.close(descriptor)


def ensure_private_parent(path: Path, uid: int, gid: int) -> None:
    parent = path.parent
    home = Path("/home/mtuinstra") if path == DESTINATION else path.parents[2]
    home_info = home.lstat()
    if (not stat.S_ISDIR(home_info.st_mode) or home.is_symlink() or home_info.st_uid != uid
            or stat.S_IMODE(home_info.st_mode) & 0o022):
        raise StageError("escrow staging home is unsafe")
    current = home
    for component in (".local", "share"):
        current /= component
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or current.is_symlink() or info.st_uid != uid:
            raise StageError("escrow staging directory is unsafe")
        os.chown(current, uid, gid)
        os.chmod(current, 0o700)
    if current != parent:
        raise StageError("escrow staging destination is not allowlisted")


def stage(destination: Path, marker: Path, uid: int, gid: int,
          sources: dict[str, Path] = SOURCES) -> str:
    if marker.exists():
        info = marker.lstat()
        if (marker.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID
                or stat.S_IMODE(info.st_mode) != 0o600
                or marker.read_text(encoding="ascii") != "schema-version=1\n"):
            raise StageError("escrow staging marker is unsafe")
        return "already-staged"
    if destination.exists() or destination.is_symlink():
        raise StageError("escrow handoff already exists without a completed marker")
    values = {name: read_secret(path) for name, path in sources.items()}
    document = {"schema_version": 1, "source_host": "sanctuary", "secrets": values}
    encoded = (json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii")
    ensure_private_parent(destination, uid, gid)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker_descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(marker_descriptor, b"schema-version=1\n")
        os.fsync(marker_descriptor)
    finally:
        os.close(marker_descriptor)
    return "staged"


def main() -> int:
    try:
        if os.geteuid() != 0:
            raise StageError("run with sudo on Sanctuary")
        account = pwd.getpwnam("mtuinstra")
        if Path(account.pw_dir) != Path("/home/mtuinstra"):
            raise StageError("mtuinstra home does not match the approved destination")
        status = stage(DESTINATION, MARKER, account.pw_uid, account.pw_gid)
        print(f"backup escrow handoff {status}; path={DESTINATION}")
        return 0
    except (KeyError, OSError, StageError) as exc:
        message = str(exc) if isinstance(exc, StageError) else "fixed filesystem operation failed"
        print(f"backup escrow staging failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
