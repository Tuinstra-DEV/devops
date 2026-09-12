#!/usr/bin/env python3
"""Escrow and recover the fixed Sanctuary production-backup credentials.

Secret input is accepted only as a bounded JSON document on stdin. 1Password
mutations use JSON stdin as well; secret values are never command arguments or
successful program output. Recovery writes a private, temporary directory that
must be explicitly removed with the cleanup command.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any


SCHEMA_VERSION = 1
ITEM_TITLE = "Tuinstra production backups — Sanctuary"
ITEM_TAG = "tuinstra-managed-production-backup-secrets-v1"
ITEM_CATEGORY = "SECURE_NOTE"
SOURCE_HOST = "sanctuary"
AGE_SECRET_PREFIX = "AGE-" + "SECRET-KEY-1"
SSH_PRIVATE_BEGIN = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
SSH_PRIVATE_END = "-----END OPENSSH " + "PRIVATE KEY-----"
MAX_DOCUMENT_BYTES = 64 * 1024
MAX_OP_OUTPUT_BYTES = 256 * 1024
RECOVERY_PREFIX = "tuinstra-backup-recovery-v1-"
RECOVERY_MARKER = ".tuinstra-backup-recovery-v1"
MARKER_CONTENT = "managed-by=tuinstra-backup-onepassword-escrow\nschema-version=1\n"
VAULT_ID_PATTERN = re.compile(r"^[a-z0-9]{26}$")
ITEM_ID_PATTERN = re.compile(r"^[a-z0-9]{26}$")


# The names and target paths form a closed allowlist. Adding a backup secret is
# an explicit schema change, rather than an arbitrary path or field argument.
SECRET_SPECS = {
    "age_identity": {
        "field_id": "tuinstraBackupAgeIdentity",
        "label": "/etc/tuinstra-backup/age-identity.txt",
        "relative_path": "etc/tuinstra-backup/age-identity.txt",
        "validator": "age",
    },
    "restic_prod01_umami": {
        "field_id": "tuinstraBackupResticProd01Umami",
        "label": "/etc/tuinstra-backup/restic-passwords/tuinstra-prod-01/umami.password",
        "relative_path": "etc/tuinstra-backup/restic-passwords/tuinstra-prod-01/umami.password",
        "validator": "restic",
    },
    "ssh_prod01": {
        "field_id": "tuinstraBackupSshProd01",
        "label": "/etc/tuinstra-backup/ssh/prod01",
        "relative_path": "etc/tuinstra-backup/ssh/prod01",
        "validator": "ssh",
    },
    "ssh_prod02": {
        "field_id": "tuinstraBackupSshProd02",
        "label": "/etc/tuinstra-backup/ssh/prod02",
        "relative_path": "etc/tuinstra-backup/ssh/prod02",
        "validator": "ssh",
    },
}
SCHEMA_FIELD_ID = "tuinstraBackupEscrowSchema"
SOURCE_FIELD_ID = "tuinstraBackupEscrowSource"
NOTES_FIELD_ID = "notesPlain"


class EscrowError(RuntimeError):
    """A deliberately sanitized error which contains no command output."""


class ProcessRunner:
    """Run a process while requiring the user's interactive 1Password session."""

    def run(self, args: list[str], *, input_text: str | None = None, timeout: int = 30):
        environment = os.environ.copy()
        for name in list(environment):
            if name in {"OP_SERVICE_ACCOUNT_TOKEN", "OP_CONNECT_HOST", "OP_CONNECT_TOKEN"} \
                    or name.startswith("OP_SESSION_"):
                environment.pop(name)
        return subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=environment,
        )


class SafeCommands:
    def __init__(self, runner: ProcessRunner):
        self.runner = runner

    def run(self, args: list[str], *, input_text: str | None = None,
            operation: str = "Command") -> str:
        try:
            result = self.runner.run(args, input_text=input_text, timeout=30)
        except subprocess.TimeoutExpired:
            raise EscrowError(f"{operation} timed out; its outcome may be uncertain") from None
        except OSError:
            raise EscrowError(f"{operation} could not be started") from None
        if result.returncode != 0:
            raise EscrowError(f"{operation} failed; raw output suppressed")
        if len(result.stdout.encode("utf-8")) > MAX_OP_OUTPUT_BYTES:
            raise EscrowError(f"{operation} returned too much data; raw output suppressed")
        return result.stdout

    def json(self, args: list[str], *, input_value: dict[str, Any] | None = None,
             operation: str = "Command") -> Any:
        input_text = None if input_value is None else json.dumps(
            input_value, ensure_ascii=False, separators=(",", ":")
        )
        output = self.run(args, input_text=input_text, operation=operation)
        try:
            return json.loads(output)
        except (TypeError, ValueError):
            raise EscrowError(f"{operation} returned invalid JSON; raw output suppressed") from None


def _field_value(item: dict[str, Any], field_id: str) -> Any:
    for field in item.get("fields", []):
        if isinstance(field, dict) and field.get("id") == field_id:
            return field.get("value")
    return None


def _set_field(item: dict[str, Any], field_id: str, field_type: str,
               label: str, value: str, *, purpose: str | None = None) -> None:
    field = {"id": field_id, "type": field_type, "label": label, "value": value}
    if purpose:
        field["purpose"] = purpose
    item.setdefault("fields", []).append(field)


def _validate_ascii(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= maximum:
        raise EscrowError(f"{label} has an invalid size")
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise EscrowError(f"{label} is not valid ASCII") from None
    if "\x00" in value or not value.endswith("\n"):
        raise EscrowError(f"{label} has an invalid encoding")
    return value


def _validate_secret(name: str, value: Any) -> str:
    kind = SECRET_SPECS[name]["validator"]
    if kind == "restic":
        value = _validate_ascii(value, "Restic password", 128)
        if not re.fullmatch(r"[a-f0-9]{64}\n", value):
            raise EscrowError("Restic password has an invalid format")
        return value
    if kind == "age":
        value = _validate_ascii(value, "age identity", 4096)
        secret_lines = [line for line in value.splitlines() if line and not line.startswith("#")]
        if len(secret_lines) != 1 or not re.fullmatch(
                re.escape(AGE_SECRET_PREFIX) + r"[0-9A-Z]{20,100}", secret_lines[0]):
            raise EscrowError("age identity has an invalid format")
        return value
    value = _validate_ascii(value, "SSH private key", 16384)
    lines = value.splitlines()
    if len(lines) < 3 or lines[0] != SSH_PRIVATE_BEGIN \
            or lines[-1] != SSH_PRIVATE_END \
            or any(not re.fullmatch(r"[A-Za-z0-9+/=]+", line) for line in lines[1:-1]):
        raise EscrowError("SSH private key has an invalid format")
    try:
        decoded = base64.b64decode("".join(lines[1:-1]), validate=True)
    except (binascii.Error, ValueError):
        raise EscrowError("SSH private key has an invalid format") from None
    if not decoded.startswith(b"openssh-key-v1\x00"):
        raise EscrowError("SSH private key has an invalid format")
    return value


def _validate_secret_set(values: dict[str, Any]) -> dict[str, str]:
    validated = {name: _validate_secret(name, values[name]) for name in SECRET_SPECS}
    if validated["ssh_prod01"] == validated["ssh_prod02"]:
        raise EscrowError("Production pull hosts must use distinct SSH identities")
    return validated


def parse_bundle(stream) -> dict[str, str]:
    raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise EscrowError("Escrow input exceeds the fixed size limit")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise EscrowError("Escrow input is not valid JSON; raw input suppressed") from None
    if not isinstance(document, dict) or set(document) != {"schema_version", "source_host", "secrets"}:
        raise EscrowError("Escrow input has an invalid schema")
    if document.get("schema_version") != SCHEMA_VERSION or document.get("source_host") != SOURCE_HOST:
        raise EscrowError("Escrow input identity does not match Sanctuary schema v1")
    values = document.get("secrets")
    if not isinstance(values, dict) or set(values) != set(SECRET_SPECS):
        raise EscrowError("Escrow input does not contain the exact credential allowlist")
    return _validate_secret_set(values)


def build_item(secrets: dict[str, str]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "title": ITEM_TITLE,
        "category": ITEM_CATEGORY,
        "tags": [ITEM_TAG],
        "fields": [],
    }
    _set_field(item, SCHEMA_FIELD_ID, "STRING", "Escrow schema", "1")
    _set_field(item, SOURCE_FIELD_ID, "STRING", "Source host", SOURCE_HOST)
    _set_field(
        item, NOTES_FIELD_ID, "STRING", "notesPlain",
        "Managed by DEV-30. Fixed Sanctuary production-backup recovery credentials; do not duplicate.",
        purpose="NOTES",
    )
    for name, spec in SECRET_SPECS.items():
        _set_field(item, spec["field_id"], "CONCEALED", spec["label"], secrets[name])
    return item


def _category_name(item: dict[str, Any]) -> Any:
    category = item.get("category")
    return category.get("id") if isinstance(category, dict) else category


def validate_managed_item(item: dict[str, Any]) -> str:
    item_id = item.get("id")
    if not isinstance(item_id, str) or not ITEM_ID_PATTERN.fullmatch(item_id):
        raise EscrowError("Managed 1Password item has an invalid identity")
    protected_ids = {SCHEMA_FIELD_ID, SOURCE_FIELD_ID} | {
        spec["field_id"] for spec in SECRET_SPECS.values()
    }
    field_ids = [field.get("id") for field in item.get("fields", []) if isinstance(field, dict)]
    if any(field_ids.count(field_id) != 1 for field_id in protected_ids):
        raise EscrowError("Managed 1Password item has missing or duplicate protected fields")
    if item.get("title") != ITEM_TITLE or _category_name(item) != ITEM_CATEGORY \
            or item.get("tags") != [ITEM_TAG] \
            or _field_value(item, SCHEMA_FIELD_ID) != "1" \
            or _field_value(item, SOURCE_FIELD_ID) != SOURCE_HOST:
        raise EscrowError("1Password title collision is not the managed escrow item")
    return item_id


def item_secrets(item: dict[str, Any]) -> dict[str, str]:
    validate_managed_item(item)
    values: dict[str, Any] = {}
    for name, spec in SECRET_SPECS.items():
        values[name] = _field_value(item, spec["field_id"])
    return _validate_secret_set(values)


class OnePassword:
    def __init__(self, executable: str, vault_id: str, commands: SafeCommands):
        if not VAULT_ID_PATTERN.fullmatch(vault_id):
            raise EscrowError("Invalid 1Password vault identity")
        executable_path = Path(executable)
        if not executable_path.is_absolute():
            raise EscrowError("1Password executable path must be absolute")
        self.executable = executable
        self.vault_id = vault_id
        self.commands = commands

    def matches(self) -> list[dict[str, Any]]:
        result = self.commands.json(
            [self.executable, "item", "list", "--vault", self.vault_id,
             "--categories", "Secure Note", "--format", "json"],
            operation="1Password item metadata listing",
        )
        if not isinstance(result, list) or any(not isinstance(entry, dict) for entry in result):
            raise EscrowError("1Password item metadata listing returned an invalid shape")
        return [entry for entry in result if entry.get("title") == ITEM_TITLE]

    def get(self, item_id: str) -> dict[str, Any]:
        if not ITEM_ID_PATTERN.fullmatch(item_id):
            raise EscrowError("Invalid 1Password item identity")
        item = self.commands.json(
            [self.executable, "item", "get", item_id, "--vault", self.vault_id,
             "--format", "json", "--reveal"],
            operation="1Password managed escrow read",
        )
        if not isinstance(item, dict):
            raise EscrowError("1Password managed escrow returned an invalid shape")
        return item

    def resolve(self) -> dict[str, Any] | None:
        matches = self.matches()
        if len(matches) > 1:
            raise EscrowError("Duplicate 1Password escrow titles found; no mutation performed")
        if not matches:
            return None
        item_id = matches[0].get("id")
        if not isinstance(item_id, str) or not ITEM_ID_PATTERN.fullmatch(item_id):
            raise EscrowError("1Password escrow metadata has an invalid identity")
        return self.get(item_id)

    def create_once(self, candidate: dict[str, Any]) -> dict[str, Any]:
        returned_id = None
        create_error = None
        try:
            created = self.commands.json(
                [self.executable, "item", "create", "-", "--vault", self.vault_id,
                 "--format", "json"],
                input_value=candidate,
                operation="1Password managed escrow creation",
            )
            if isinstance(created, dict):
                returned_id = created.get("id")
        except EscrowError as error:
            create_error = error

        # Creation may have committed even if its response failed. Resolve once
        # by immutable title and never retry the mutation.
        try:
            current = self.resolve()
        except EscrowError:
            if create_error:
                raise create_error
            raise
        if current is None:
            raise EscrowError(
                "1Password creation outcome is uncertain; managed item not found"
            ) from create_error
        resolved_id = validate_managed_item(current)
        if returned_id is not None and returned_id != resolved_id:
            raise EscrowError("1Password creation returned a conflicting item identity")
        return current


class BackupEscrow:
    def __init__(self, executable: str, vault_id: str, *, runner: ProcessRunner | None = None):
        commands = SafeCommands(runner or ProcessRunner())
        self.onepassword = OnePassword(executable, vault_id, commands)

    def escrow(self, secrets: dict[str, str]) -> dict[str, Any]:
        current = self.onepassword.resolve()
        created = current is None
        if current is None:
            current = self.onepassword.create_once(build_item(secrets))
        stored = item_secrets(current)
        if stored != secrets:
            raise EscrowError("Managed escrow exists with different credentials; refusing overwrite")
        return {
            "itemId": validate_managed_item(current),
            "created": created,
            "escrowed": True,
            "verified": True,
            "secretCount": len(stored),
        }

    def recover(self) -> tuple[str, dict[str, str]]:
        current = self.onepassword.resolve()
        if current is None:
            raise EscrowError("Managed 1Password escrow item was not found")
        return validate_managed_item(current), item_secrets(current)


def _mkdir_private(path: Path) -> None:
    path.mkdir(mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise EscrowError("Recovery directory ownership is unsafe")
    os.chmod(path, 0o700)


def _write_private(path: Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        encoded = value.encode("ascii")
        offset = 0
        while offset < len(encoded):
            offset += os.write(descriptor, encoded[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)


def materialize(secrets: dict[str, str], recovery_root: str | None = None) -> Path:
    root = Path(recovery_root or tempfile.gettempdir())
    if root.is_symlink() or not root.is_dir():
        raise EscrowError("Recovery root must be an existing directory")
    directory = Path(tempfile.mkdtemp(prefix=RECOVERY_PREFIX, dir=root))
    try:
        os.chmod(directory, 0o700)
        directories = sorted(
            {Path(spec["relative_path"]).parent for spec in SECRET_SPECS.values()},
            key=lambda path: len(path.parts),
        )
        for relative in directories:
            current = directory
            for part in relative.parts:
                current /= part
                if not current.exists():
                    _mkdir_private(current)
                elif current.is_symlink() or not current.is_dir():
                    raise EscrowError("Recovery path contains an unsafe component")
        _write_private(directory / RECOVERY_MARKER, MARKER_CONTENT)
        for name, spec in SECRET_SPECS.items():
            destination = directory / spec["relative_path"]
            _write_private(destination, secrets[name])
            if destination.read_text(encoding="ascii") != secrets[name]:
                raise EscrowError("Recovered credential could not be verified")
        return directory
    except Exception:
        _cleanup_known_tree(directory, require_complete=False)
        raise


def _expected_relative_paths() -> set[str]:
    return {spec["relative_path"] for spec in SECRET_SPECS.values()} | {RECOVERY_MARKER}


def _validate_recovery_tree(directory: Path, *, require_complete: bool) -> None:
    if not directory.name.startswith(RECOVERY_PREFIX) or directory.is_symlink():
        raise EscrowError("Cleanup target is not a managed recovery directory")
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() \
            or stat.S_IMODE(info.st_mode) != 0o700:
        raise EscrowError("Cleanup target ownership or permissions are unsafe")
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    for root, directory_names, file_names in os.walk(directory, followlinks=False):
        current = Path(root)
        for name in directory_names:
            child = current / name
            if child.is_symlink():
                raise EscrowError("Recovery directory contains a symbolic link")
            actual_directories.add(str(child.relative_to(directory)))
        for name in file_names:
            child = current / name
            if child.is_symlink() or not child.is_file():
                raise EscrowError("Recovery directory contains an unsafe file")
            actual_files.add(str(child.relative_to(directory)))
    expected = _expected_relative_paths()
    if actual_files - expected or (require_complete and actual_files != expected):
        raise EscrowError("Recovery directory contains unexpected or missing files")
    expected_directories: set[str] = set()
    for path in expected:
        parent = Path(path).parent
        while str(parent) != ".":
            expected_directories.add(str(parent))
            parent = parent.parent
    if actual_directories - expected_directories:
        raise EscrowError("Recovery directory contains unexpected directories")
    marker = directory / RECOVERY_MARKER
    if require_complete and marker.read_text(encoding="ascii") != MARKER_CONTENT:
        raise EscrowError("Recovery directory marker is invalid")


def _cleanup_known_tree(directory: Path, *, require_complete: bool = True) -> None:
    if not directory.exists():
        if require_complete:
            raise EscrowError("Recovery directory does not exist")
        return
    _validate_recovery_tree(directory, require_complete=require_complete)
    for relative in _expected_relative_paths():
        path = directory / relative
        if path.exists():
            path.unlink()
    all_directories = [Path(root) / name for root, names, _files in os.walk(directory) for name in names]
    for path in sorted(all_directories, key=lambda value: len(value.parts), reverse=True):
        path.rmdir()
    directory.rmdir()


def cleanup(recovery_dir: str) -> None:
    directory = Path(recovery_dir)
    if not directory.is_absolute():
        raise EscrowError("Recovery directory path must be absolute")
    _cleanup_known_tree(directory)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Escrow fixed production-backup credentials")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("escrow", "restore"):
        command = subparsers.add_parser(name)
        command.add_argument("--op", required=True, help="absolute path to the 1Password CLI")
        command.add_argument("--vault", required=True, help="immutable 1Password vault ID")
        if name == "restore":
            command.add_argument("--recovery-root", help="existing parent for the private temp directory")
    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--recovery-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "cleanup":
            cleanup(args.recovery_dir)
            result = {"cleaned": True}
        else:
            service = BackupEscrow(args.op, args.vault)
            if args.command == "escrow":
                secrets = parse_bundle(sys.stdin.buffer)
                result = service.escrow(secrets)
            else:
                item_id, secrets = service.recover()
                directory = materialize(secrets, args.recovery_root)
                result = {
                    "itemId": item_id,
                    "restored": True,
                    "verified": True,
                    "recoveryDir": str(directory),
                    "ownerUid": os.geteuid(),
                    "fileCount": len(secrets),
                    "cleanupRequired": True,
                }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (EscrowError, OSError) as error:
        message = str(error) if isinstance(error, EscrowError) else "filesystem operation failed"
        print(f"backup credential escrow failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
