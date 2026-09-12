#!/usr/bin/env python3
"""Secret-boundary and recovery tests for the backup 1Password escrow."""

from __future__ import annotations

import importlib.util
import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).parents[1] / "backup" / "onepassword-escrow.py"
SPEC = importlib.util.spec_from_file_location("backup_onepassword_escrow", SOURCE)
escrow = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(escrow)

ITEM_ID = "abcdefghijklmnopqrstuvwxzy"
VAULT_ID = "zyxwvutsrqponmlkjihgfedcba"
AGE = "# created: 2026-09-12T00:00:00Z\n# public key: age1example\nAGE-SECRET-KEY-1" + "A" * 32 + "\n"
RESTIC = "a" * 64 + "\n"
SSH_ONE_BODY = base64.b64encode(b"openssh-key-v1\0" + b"one" * 32).decode()
SSH_TWO_BODY = base64.b64encode(b"openssh-key-v1\0" + b"two" * 32).decode()
SSH_RESTORE_BODY = base64.b64encode(b"openssh-key-v1\0" + b"restore" * 32).decode()
SSH_ONE = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{SSH_ONE_BODY}\n-----END OPENSSH PRIVATE KEY-----\n"
SSH_TWO = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{SSH_TWO_BODY}\n-----END OPENSSH PRIVATE KEY-----\n"
SSH_RESTORE = f"-----BEGIN OPENSSH PRIVATE KEY-----\n{SSH_RESTORE_BODY}\n-----END OPENSSH PRIVATE KEY-----\n"
SECRETS = {
    "age_identity": AGE,
    "restic_prod01_umami": RESTIC,
    "ssh_prod01": SSH_ONE,
    "ssh_prod02": SSH_TWO,
    "ssh_prod01_restore": SSH_RESTORE,
}
LEGACY_SECRETS = {name: value for name, value in SECRETS.items() if name != "ssh_prod01_restore"}


def bundle(overrides=None):
    values = dict(SECRETS)
    values.update(overrides or {})
    return {
        "schema_version": 1,
        "source_host": "sanctuary",
        "secrets": values,
    }


class FakeRunner:
    def __init__(self, *, existing=None, duplicate=False, uncertain_create=False,
                 uncertain_create_timeout=False, truncate_readback=False,
                 command_secret_error=False, uncertain_edit=False):
        self.item = existing
        self.duplicate = duplicate
        self.uncertain_create = uncertain_create
        self.uncertain_create_timeout = uncertain_create_timeout
        self.truncate_readback = truncate_readback
        self.command_secret_error = command_secret_error
        self.uncertain_edit = uncertain_edit
        self.calls = []
        self.timeouts = []

    @staticmethod
    def result(args, returncode=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)

    def run(self, args, *, input_text=None, timeout=30):
        args = list(args)
        self.calls.append((args, input_text))
        self.timeouts.append(timeout)
        if args[1:3] == ["item", "list"]:
            entries = [] if self.item is None else [{"id": ITEM_ID, "title": escrow.ITEM_TITLE}]
            if self.duplicate:
                entries.append({"id": "b" * 26, "title": escrow.ITEM_TITLE})
            return self.result(args, stdout=json.dumps(entries))
        if args[1:3] == ["item", "create"]:
            self.item = json.loads(input_text)
            self.item["id"] = ITEM_ID
            if self.uncertain_create_timeout:
                raise subprocess.TimeoutExpired(
                    args,
                    timeout,
                    output=RESTIC,
                    stderr=SSH_ONE,
                )
            if self.uncertain_create:
                error = RESTIC if self.command_secret_error else "failed"
                return self.result(args, returncode=1, stderr=error)
            return self.result(args, stdout=json.dumps({"id": ITEM_ID}))
        if args[1:3] == ["item", "edit"]:
            self.item = json.loads(input_text)
            self.item["id"] = ITEM_ID
            if self.uncertain_edit:
                return self.result(args, returncode=1, stderr=SSH_RESTORE)
            return self.result(args, stdout=json.dumps({"id": ITEM_ID}))
        if args[1:3] == ["item", "get"]:
            value = json.loads(json.dumps(self.item))
            if self.truncate_readback:
                value["fields"] = [
                    field for field in value["fields"]
                    if field.get("id") != escrow.SECRET_SPECS["ssh_prod02"]["field_id"]
                ]
            return self.result(args, stdout=json.dumps(value))
        raise AssertionError(f"unexpected command: {args}")


class BackupOnePasswordEscrowTests(unittest.TestCase):
    def make_service(self, runner):
        return escrow.BackupEscrow("/opt/op", VAULT_ID, runner=runner)

    def test_bundle_schema_and_secret_formats_are_closed(self):
        parsed = escrow.parse_bundle(_BytesInput(json.dumps(bundle()).encode()))
        self.assertEqual(parsed, SECRETS)

        invalid = bundle()
        invalid["secrets"]["unexpected"] = "secret\n"
        with self.assertRaisesRegex(escrow.EscrowError, "exact credential allowlist"):
            escrow.parse_bundle(_BytesInput(json.dumps(invalid).encode()))
        with self.assertRaisesRegex(escrow.EscrowError, "Restic password"):
            escrow.parse_bundle(_BytesInput(json.dumps(bundle({"restic_prod01_umami": "weak\n"})).encode()))

    def test_create_and_readback_keep_all_secrets_out_of_arguments(self):
        runner = FakeRunner()
        result = self.make_service(runner).escrow(SECRETS)

        self.assertEqual(result, {
            "itemId": ITEM_ID, "created": True, "escrowed": True,
            "verified": True, "secretCount": 5,
        })
        arguments = "\n".join(" ".join(args) for args, _input in runner.calls)
        for secret in SECRETS.values():
            self.assertNotIn(secret.strip(), arguments)
        create_inputs = [input_text for args, input_text in runner.calls if args[1:3] == ["item", "create"]]
        self.assertEqual(len(create_inputs), 1)
        created_item = {**json.loads(create_inputs[0]), "id": ITEM_ID}
        self.assertEqual(escrow.item_secrets(created_item), SECRETS)
        self.assertFalse(any(args[1:3] == ["item", "edit"] for args, _ in runner.calls))

    def test_existing_four_field_item_is_upgraded_with_restore_key_via_stdin_and_read_back(self):
        existing = escrow.build_item(LEGACY_SECRETS)
        existing["id"] = ITEM_ID
        runner = FakeRunner(existing=existing)

        result = self.make_service(runner).escrow(SECRETS)

        self.assertFalse(result["created"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["secretCount"], 5)
        edits = [(args, input_text) for args, input_text in runner.calls if args[1:3] == ["item", "edit"]]
        self.assertEqual(len(edits), 1)
        self.assertIn("--template=-", edits[0][0])
        for secret in SECRETS.values():
            self.assertNotIn(secret.strip(), " ".join(edits[0][0]))
        self.assertEqual(escrow.item_secrets(runner.item), SECRETS)

    def test_legacy_four_field_bundle_remains_recoverable(self):
        document = {"schema_version": 1, "source_host": "sanctuary", "secrets": LEGACY_SECRETS}
        parsed = escrow.parse_bundle(_BytesInput(json.dumps(document).encode()))
        self.assertEqual(parsed, LEGACY_SECRETS)

    def test_restore_identity_must_be_distinct_from_pull_identities(self):
        duplicate = bundle({"ssh_prod01_restore": SSH_ONE})
        with self.assertRaisesRegex(escrow.EscrowError, "restore must use a distinct"):
            escrow.parse_bundle(_BytesInput(json.dumps(duplicate).encode()))

    def test_uncertain_upgrade_is_reconciled_once_without_secret_output(self):
        existing = escrow.build_item(LEGACY_SECRETS)
        existing["id"] = ITEM_ID
        runner = FakeRunner(existing=existing, uncertain_edit=True)

        result = self.make_service(runner).escrow(SECRETS)

        self.assertTrue(result["verified"])
        self.assertEqual(sum(args[1:3] == ["item", "edit"] for args, _ in runner.calls), 1)

    def test_identical_existing_item_is_idempotently_reused_without_mutation(self):
        existing = escrow.build_item(SECRETS)
        existing["id"] = ITEM_ID
        runner = FakeRunner(existing=existing)

        result = self.make_service(runner).escrow(SECRETS)

        self.assertFalse(result["created"])
        self.assertTrue(result["verified"])
        self.assertFalse(any(args[1:3] in (["item", "create"], ["item", "edit"])
                             for args, _ in runner.calls))

    def test_existing_different_credentials_are_never_overwritten(self):
        different = dict(SECRETS)
        different["restic_prod01_umami"] = "b" * 64 + "\n"
        existing = escrow.build_item(different)
        existing["id"] = ITEM_ID
        runner = FakeRunner(existing=existing)

        with self.assertRaisesRegex(escrow.EscrowError, "refusing overwrite"):
            self.make_service(runner).escrow(SECRETS)
        self.assertFalse(any(args[1:3] in (["item", "create"], ["item", "edit"])
                             for args, _ in runner.calls))

    def test_duplicate_title_fails_before_any_mutation(self):
        existing = escrow.build_item(SECRETS)
        existing["id"] = ITEM_ID
        runner = FakeRunner(existing=existing, duplicate=True)

        with self.assertRaisesRegex(escrow.EscrowError, "Duplicate"):
            self.make_service(runner).escrow(SECRETS)
        self.assertFalse(any(args[1:3] in (["item", "create"], ["item", "edit"])
                             for args, _ in runner.calls))

    def test_uncertain_create_is_resolved_once_and_secret_output_is_suppressed(self):
        runner = FakeRunner(uncertain_create=True, command_secret_error=True)
        result = self.make_service(runner).escrow(SECRETS)

        self.assertEqual(result["itemId"], ITEM_ID)
        self.assertEqual(sum(args[1:3] == ["item", "create"] for args, _ in runner.calls), 1)

    def test_timed_out_create_is_reconciled_without_retry_or_secret_output(self):
        runner = FakeRunner(uncertain_create_timeout=True)

        result = self.make_service(runner).escrow(SECRETS)

        self.assertEqual(result["itemId"], ITEM_ID)
        self.assertTrue(result["verified"])
        self.assertEqual(sum(args[1:3] == ["item", "create"] for args, _ in runner.calls), 1)
        self.assertEqual(runner.timeouts, [120] * len(runner.timeouts))

    def test_partial_or_invalid_readback_fails_closed(self):
        runner = FakeRunner(truncate_readback=True)
        with self.assertRaises(escrow.EscrowError) as caught:
            self.make_service(runner).escrow(SECRETS)
        self.assertNotIn(SSH_TWO.strip(), str(caught.exception))
        self.assertEqual(sum(args[1:3] == ["item", "create"] for args, _ in runner.calls), 1)

    def test_recovery_materializes_vault_values_mode_0600_and_cleanup_is_explicit(self):
        existing = escrow.build_item(SECRETS)
        existing["id"] = ITEM_ID
        service = self.make_service(FakeRunner(existing=existing))
        item_id, recovered = service.recover()
        self.assertEqual(item_id, ITEM_ID)

        with tempfile.TemporaryDirectory() as parent:
            directory = escrow.materialize(recovered, parent)
            self.assertTrue(directory.exists())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            for name, spec in escrow.SECRET_SPECS.items():
                path = directory / spec["relative_path"]
                self.assertEqual(path.read_text(encoding="ascii"), SECRETS[name])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            escrow.cleanup(str(directory))
            self.assertFalse(directory.exists())

    def test_cleanup_rejects_unexpected_files_and_symbolic_links(self):
        with tempfile.TemporaryDirectory() as parent:
            directory = escrow.materialize(SECRETS, parent)
            (directory / "unexpected").write_text("not secret", encoding="ascii")
            with self.assertRaisesRegex(escrow.EscrowError, "unexpected"):
                escrow.cleanup(str(directory))
            (directory / "unexpected").unlink()
            link = directory / "unsafe-link"
            link.symlink_to(directory / escrow.RECOVERY_MARKER)
            with self.assertRaises(escrow.EscrowError):
                escrow.cleanup(str(directory))
            link.unlink()
            escrow.cleanup(str(directory))

    def test_process_runner_strips_noninteractive_op_credentials(self):
        completed = subprocess.CompletedProcess([], 0, "", "")
        injected = {
            "OP_SERVICE_ACCOUNT_TOKEN": RESTIC,
            "OP_CONNECT_TOKEN": SSH_ONE,
            "OP_CONNECT_HOST": "https://connect.invalid",
            "OP_SESSION_example": AGE,
        }
        with patch.dict(os.environ, injected), patch.object(
                escrow.subprocess, "run", return_value=completed) as run:
            escrow.ProcessRunner().run(["/opt/op", "--version"])
        environment = run.call_args.kwargs["env"]
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        for name in injected:
            self.assertNotIn(name, environment)

    def test_all_op_calls_use_the_fixed_desktop_approval_timeout(self):
        runner = FakeRunner()

        self.make_service(runner).escrow(SECRETS)

        self.assertGreaterEqual(len(runner.timeouts), 3)
        self.assertEqual(runner.timeouts, [120] * len(runner.timeouts))

    def test_sanitized_command_failure_never_includes_process_output(self):
        class FailedRunner:
            def run(self, args, *, input_text=None, timeout=30):
                return subprocess.CompletedProcess(args, 1, RESTIC, SSH_ONE)

        commands = escrow.SafeCommands(FailedRunner())
        with self.assertRaises(escrow.EscrowError) as caught:
            commands.run(["/opt/op", "item", "list"])
        self.assertNotIn(RESTIC.strip(), str(caught.exception))
        self.assertNotIn(SSH_ONE.strip(), str(caught.exception))

    def test_sanitized_timeout_never_includes_process_output(self):
        class TimedOutRunner:
            def run(self, args, *, input_text=None, timeout=30):
                raise subprocess.TimeoutExpired(
                    args,
                    timeout,
                    output=RESTIC,
                    stderr=SSH_ONE,
                )

        commands = escrow.SafeCommands(TimedOutRunner())
        with self.assertRaisesRegex(escrow.EscrowError, "timed out") as caught:
            commands.run(["/opt/op", "item", "list"])
        self.assertNotIn(RESTIC.strip(), str(caught.exception))
        self.assertNotIn(SSH_ONE.strip(), str(caught.exception))


class _BytesInput:
    def __init__(self, value):
        self.value = value

    def read(self, amount):
        return self.value[:amount]


if __name__ == "__main__":
    unittest.main()
