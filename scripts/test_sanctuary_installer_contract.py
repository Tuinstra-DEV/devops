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

    def test_worker_owns_only_host_keys_while_other_credentials_remain_root_only(self):
        self.assertIn(
            "chown tuinstra-backup:tuinstra-backup /etc/tuinstra-backup/ssh/prod01 /etc/tuinstra-backup/ssh/prod02",
            INSTALLER,
        )
        self.assertIn("owner: tuinstra-backup, group: tuinstra-backup, mode: '0600'", ROLE)
        self.assertIn("validate_secret(destination, ssh_credential_uids())", BOOTSTRAP)
        self.assertIn("chown root:root /etc/tuinstra-backup/age-identity.txt", INSTALLER)

    def test_root_owns_lock_parent_and_worker_only_owns_pull_subdirectory(self):
        commands = [line.strip() for line in INSTALLER.replace("\\\n", " ").splitlines()]
        self.assertIn("install -d -o root -g root -m 0711 /var/lib/tuinstra-backup/locks", commands)
        worker_commands = [line for line in commands if line.startswith(
            "install -d -o tuinstra-backup -g tuinstra-backup")]
        self.assertEqual(len(worker_commands), 1)
        worker_paths = worker_commands[0].split()[8:]
        self.assertNotIn("/var/lib/tuinstra-backup/locks", worker_paths)
        self.assertIn("/var/lib/tuinstra-backup/locks/pull", worker_paths)
        self.assertIn("path: /var/lib/tuinstra-backup/locks, owner: root, group: root, mode: '0711'", ROLE)


if __name__ == "__main__":
    unittest.main()
