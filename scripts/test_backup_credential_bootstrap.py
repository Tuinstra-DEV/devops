#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
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


if __name__ == "__main__":
    unittest.main()
