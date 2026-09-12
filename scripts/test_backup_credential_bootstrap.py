#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).with_name("bootstrap_backup_credentials.py")
SPEC = importlib.util.spec_from_file_location("backup_bootstrap", SOURCE)
bootstrap = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(bootstrap)


class CredentialBootstrapTests(unittest.TestCase):
    def test_mount_must_be_exact_active_destination_with_capacity(self):
        with mock.patch.object(bootstrap.Path, "is_mount", return_value=False):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "mountpoint"):
                bootstrap.validate_mount()
        with mock.patch.object(bootstrap.Path, "is_mount", return_value=True), \
             mock.patch.object(bootstrap.shutil, "disk_usage", return_value=mock.Mock(free=99 * 1024**3)):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "100 GiB"):
                bootstrap.validate_mount()

    def test_restic_password_is_created_once_and_never_rotated_implicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            secret_root = Path(temporary)
            destination = secret_root / "restic-passwords/tuinstra-prod-01/umami.password"
            destination.parent.mkdir(parents=True)

            def validate(path):
                self.assertEqual(path, destination)
                self.assertTrue(path.is_file())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

            with mock.patch.object(bootstrap, "SECRET_ROOT", secret_root), \
                 mock.patch.object(bootstrap, "validate_secret", side_effect=validate), \
                 mock.patch.object(bootstrap.os, "chown"):
                bootstrap.ensure_restic_password()
                first = destination.read_bytes()
                bootstrap.ensure_restic_password()
            self.assertEqual(destination.read_bytes(), first)
            self.assertEqual(len(first.strip()), 64)

    def test_non_root_execution_is_rejected(self):
        with mock.patch.object(bootstrap.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "sudo"):
                bootstrap.require_root()

    def test_main_generates_distinct_pull_and_production_restore_identities(self):
        identities = {
            name: Path('/etc/tuinstra-backup/ssh') / name
            for name in ('prod01', 'prod02', 'prod01-restore')
        }
        with mock.patch.object(bootstrap, 'require_root'), \
             mock.patch.object(bootstrap.shutil, 'which', return_value='/usr/bin/tool'), \
             mock.patch.object(bootstrap, 'validate_mount', return_value=200 * 1024**3), \
             mock.patch.object(bootstrap.Path, 'mkdir'), \
             mock.patch.object(bootstrap.os, 'chown'), \
             mock.patch.object(bootstrap.os, 'chmod'), \
             mock.patch.object(bootstrap, 'ensure_age_identity'), \
             mock.patch.object(bootstrap, 'ensure_ssh_identity', side_effect=identities.get) as ensure, \
             mock.patch.object(bootstrap, 'require_distinct_ssh_identities') as distinct, \
             mock.patch.object(bootstrap, 'ensure_restic_password'):
            self.assertEqual(bootstrap.main(), 0)

        self.assertEqual(
            [call.args[0] for call in ensure.call_args_list],
            ['prod01', 'prod02', 'prod01-restore'],
        )
        distinct.assert_called_once_with(list(identities.values()))

    def test_duplicate_pull_or_restore_identity_is_rejected(self):
        identities = [Path('/keys/prod01'), Path('/keys/prod02'), Path('/keys/prod01-restore')]
        with mock.patch.object(bootstrap, 'run_public', side_effect=[b'key-a\n', b'key-b\n', b'key-a\n']):
            with self.assertRaisesRegex(bootstrap.BootstrapError, 'must be distinct'):
                bootstrap.require_distinct_ssh_identities(identities)

    def test_legacy_service_owned_identity_is_normalized_before_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            secret_root = Path(temporary)
            identity = secret_root / "ssh/prod01"
            identity.parent.mkdir()
            original = b"preserved-private-key\n"
            identity.write_bytes(original)
            identity.chmod(0o600)
            legacy_uid = os.getuid()
            legacy = mock.Mock(st_mode=stat.S_IFREG | 0o600, st_uid=legacy_uid, st_gid=legacy_uid)
            normalized = mock.Mock(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=0)

            with mock.patch.object(bootstrap, "SECRET_ROOT", secret_root), \
                 mock.patch.object(bootstrap, "ssh_credential_uids", return_value={0, legacy_uid}), \
                 mock.patch.object(bootstrap.os, "open", return_value=41) as open_file, \
                 mock.patch.object(bootstrap.os, "fstat", side_effect=[legacy, normalized]), \
                 mock.patch.object(bootstrap.os, "fchown") as chown_file, \
                 mock.patch.object(bootstrap.os, "fchmod") as chmod_file, \
                 mock.patch.object(bootstrap.os, "close") as close_file, \
                 mock.patch.object(bootstrap, "run_public", return_value=b"ssh-ed25519 public"), \
                 mock.patch.object(bootstrap, "atomic_public"):
                self.assertEqual(bootstrap.ensure_ssh_identity("prod01"), identity)

            open_file.assert_called_once_with(
                identity, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            )
            chown_file.assert_called_once_with(41, 0, 0)
            chmod_file.assert_called_once_with(41, 0o600)
            close_file.assert_called_once_with(41)
            self.assertEqual(identity.read_bytes(), original)

    def test_ssh_identity_migration_rejects_unknown_owner_without_mutation(self):
        identity = bootstrap.SECRET_ROOT / "ssh/prod01"
        unsafe = mock.Mock(st_mode=stat.S_IFREG | 0o600, st_uid=9384, st_gid=9384)
        with mock.patch.object(bootstrap, "ssh_credential_uids", return_value={0, 993}), \
             mock.patch.object(bootstrap.os, "open", return_value=42), \
             mock.patch.object(bootstrap.os, "fstat", return_value=unsafe), \
             mock.patch.object(bootstrap.os, "fchown") as chown_file, \
             mock.patch.object(bootstrap.os, "fchmod") as chmod_file, \
             mock.patch.object(bootstrap.os, "close"):
            with self.assertRaisesRegex(bootstrap.BootstrapError, "unexpected ownership"):
                bootstrap.normalize_ssh_identity(identity)
        chown_file.assert_not_called()
        chmod_file.assert_not_called()

    def test_ssh_identity_migration_accepts_only_fixed_paths_and_names(self):
        with self.assertRaisesRegex(bootstrap.BootstrapError, "not allowlisted"):
            bootstrap.normalize_ssh_identity(Path("/tmp/prod01"))
        with self.assertRaisesRegex(bootstrap.BootstrapError, "name is not allowlisted"):
            bootstrap.ensure_ssh_identity("../admin")


if __name__ == "__main__":
    unittest.main()
