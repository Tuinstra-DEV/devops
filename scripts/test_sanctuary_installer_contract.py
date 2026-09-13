#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "scripts/install-sanctuary-backups").read_text(encoding="utf-8")
ROLE = (ROOT / "infra/ansible/roles/sanctuary_backup/tasks/main.yml").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "scripts/bootstrap_backup_credentials.py").read_text(encoding="utf-8")


class SanctuaryInstallerContractTests(unittest.TestCase):
    def test_worker_account_exists_before_supplementary_group_assignment(self):
        account_guard = INSTALLER.index("if ! id tuinstra-backup")
        useradd = INSTALLER.index("useradd --system --gid tuinstra-backup")
        usermod = INSTALLER.index("usermod -a -G tuinstra-ops tuinstra-backup")
        self.assertLess(account_guard, useradd)
        self.assertLess(useradd, usermod)

    def test_complete_root_cycle_owns_host_keys_and_other_credentials(self):
        self.assertIn(
            "chown root:root /etc/tuinstra-backup/ssh/prod01 /etc/tuinstra-backup/ssh/prod02",
            INSTALLER,
        )
        self.assertIn('chown root:root "$known_hosts_temp"', INSTALLER)
        self.assertIn("path: /etc/tuinstra-backup/ssh/prod01, owner: root, group: root, mode: '0600'", ROLE)
        self.assertIn("validate_secret(destination, ssh_credential_uids())", BOOTSTRAP)
        self.assertIn("chown root:root /etc/tuinstra-backup/age-identity.txt", INSTALLER)
        self.assertIn("rm -f /etc/sudoers.d/93-tuinstra-backup-ingest", INSTALLER)
        self.assertNotIn("tuinstra-backup ALL=(root)", INSTALLER)

    def test_root_owns_lock_parent_and_all_cycle_state(self):
        commands = [line.strip() for line in INSTALLER.replace("\\\n", " ").splitlines()]
        self.assertIn("install -d -o root -g root -m 0711 /var/lib/tuinstra-backup/locks", commands)
        worker_commands = [line for line in commands if line.startswith(
            "install -d -o tuinstra-backup -g tuinstra-backup")]
        self.assertEqual(worker_commands, [])
        root_cycle_commands = [line for line in commands if line.startswith("install -d -o root -g root -m 0700")]
        self.assertTrue(any("/var/lib/tuinstra-backup/locks/pull" in line for line in root_cycle_commands))
        self.assertIn("path: /var/lib/tuinstra-backup/locks, owner: root, group: root, mode: '0711'", ROLE)
        self.assertIn("path: /var/lib/tuinstra-backup/locks/pull, owner: root, group: root, mode: '0700'", ROLE)

    def test_tmpfiles_recreates_root_private_restore_runtime_on_boot(self):
        self.assertIn("d /run/tuinstra-backup 0700 root root -", INSTALLER)
        self.assertIn("d /run/tuinstra-backup 0700 root root -", ROLE)

    def test_tracker_restore_adapter_is_installed_without_changing_umami_adapter(self):
        self.assertIn('restore-tracker', INSTALLER)
        self.assertIn('restore-tracker', ROLE)
        self.assertIn('restore-umami', INSTALLER)
        self.assertIn('restore-umami', ROLE)


if __name__ == "__main__":
    unittest.main()
