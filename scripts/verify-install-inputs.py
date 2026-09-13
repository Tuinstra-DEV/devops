#!/usr/bin/env python3
"""Verify external files staged into a reviewed installer bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


MANIFEST_SCHEMA_VERSION = 1
KEY_KIND = "ssh-ed25519-host-public-key"
SHA256_LENGTH = 64


class InputError(Exception):
    """Raised when an external installer input is unsafe or mismatched."""


def load_manifest(path: Path) -> list[dict[str, str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"cannot read install-input manifest: {path}") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "algorithm", "files"}:
        raise InputError("install-input manifest schema is invalid")
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION or value.get("algorithm") != "sha256":
        raise InputError("install-input manifest version or algorithm is invalid")
    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise InputError("install-input manifest must contain files")
    required = {"path", "kind", "owner", "group", "mode", "sha256", "fingerprint", "host"}
    result: list[dict[str, str]] = []
    paths: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != required or any(not isinstance(v, str) for v in item.values()):
            raise InputError("install-input manifest entry schema is invalid")
        if item["path"] in paths or not item["path"].startswith("install-input/"):
            raise InputError("install-input manifest contains an unsafe or duplicate path")
        if item["kind"] != KEY_KIND or item["mode"] != "0600":
            raise InputError("install-input manifest entry policy is invalid")
        if len(item["sha256"]) != SHA256_LENGTH or any(c not in "0123456789abcdef" for c in item["sha256"]):
            raise InputError("install-input manifest checksum is invalid")
        if not item["fingerprint"].startswith("SHA256:"):
            raise InputError("install-input manifest fingerprint is invalid")
        paths.add(item["path"])
        result.append(item)
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path) -> str:
    try:
        completed = subprocess.run(
            ["/usr/bin/ssh-keygen", "-lf", str(path), "-E", "sha256"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InputError(f"invalid SSH host-key input: {path}") from exc
    fields = completed.stdout.split()
    if len(fields) < 2:
        raise InputError(f"invalid SSH host-key fingerprint output: {path}")
    return fields[1]


def verify(root: Path, manifest_path: Path) -> None:
    entries = load_manifest(manifest_path)
    for entry in entries:
        path = root / entry["path"]
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise InputError(f"installer input is missing: {path}") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise InputError(f"installer input is not a regular file: {path}")
        if os.path.realpath(path) != str(path.resolve()):
            raise InputError(f"installer input resolves outside its bundle path: {path}")
        owner = str(metadata.st_uid)
        group = str(metadata.st_gid)
        try:
            import pwd
            import grp
            owner = pwd.getpwuid(metadata.st_uid).pw_name
            group = grp.getgrgid(metadata.st_gid).gr_name
        except (KeyError, ImportError):
            pass
        if owner != entry["owner"] or group != entry["group"]:
            raise InputError(f"installer input owner/group mismatch: {path}")
        if stat.S_IMODE(metadata.st_mode) != int(entry["mode"], 8):
            raise InputError(f"installer input mode mismatch: {path}")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise InputError(f"installer input is not valid text: {path}") from exc
        if len(lines) != 1:
            raise InputError(f"installer input is not a single Ed25519 public key: {path}")
        fields = lines[0].split()
        if len(fields) not in (2, 3) or fields[0] != "ssh-ed25519":
            raise InputError(f"installer input is not a single Ed25519 public key: {path}")
        if sha256(path) != entry["sha256"]:
            raise InputError(f"installer input checksum mismatch: {path}")
        if fingerprint(path) != entry["fingerprint"]:
            raise InputError(f"installer input fingerprint mismatch: {path}")
        print(f"verified {entry['host']} {entry['fingerprint']} {entry['sha256']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify(args.root.resolve(), args.manifest.resolve())
    except (InputError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
