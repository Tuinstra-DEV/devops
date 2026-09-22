#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import grp
import json
import os
import pwd
import stat
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "scripts/install-sanctuary-backups").read_text(encoding="utf-8")
ROLE = (ROOT / "infra/ansible/roles/sanctuary_backup/tasks/main.yml").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "scripts/bootstrap_backup_credentials.py").read_text(encoding="utf-8")
INPUT_VERIFIER = ROOT / "scripts/verify-install-inputs.py"
INPUT_MANIFEST = ROOT / "install-input/manifest.json"


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
        self.assertIn("normalize_ssh_identity(destination)", BOOTSTRAP)
        self.assertIn("chown root:root /etc/tuinstra-backup/age-identity.txt", INSTALLER)
        self.assertIn("rm -f /etc/sudoers.d/93-tuinstra-backup-ingest", INSTALLER)
        self.assertNotIn("tuinstra-backup ALL=(root)", INSTALLER)
        self.assertIn('ensure_ssh_identity("prod01-restore")', BOOTSTRAP)
        self.assertIn('PUBLIC_ROOT / f"{host}.pub"', BOOTSTRAP)

    def test_legacy_key_ownership_is_normalized_before_escrow_staging(self):
        bootstrap = INSTALLER.index('python3 "$bootstrap_source"')
        escrow = INSTALLER.index('python3 "$escrow_stage_source"')
        later_installer_chown = INSTALLER.index(
            "chown root:root /etc/tuinstra-backup/ssh/prod01 /etc/tuinstra-backup/ssh/prod02"
        )
        self.assertLess(bootstrap, escrow)
        self.assertLess(escrow, later_installer_chown)
        self.assertIn("os.fchown(descriptor, ROOT_UID, ROOT_GID)", BOOTSTRAP)

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

    def test_tracker_activation_installs_separate_credential_repository_and_policy(self):
        self.assertIn(
            "/etc/tuinstra-backup/restic-passwords/tuinstra-prod-02/tracker.password",
            INSTALLER,
        )
        self.assertIn(
            "/mnt/hdd1000-01/backups/production/tuinstra-prod-02/tracker",
            INSTALLER,
        )
        self.assertIn(
            "--host tuinstra-prod-02 --app tracker --policy-version production-v1",
            INSTALLER,
        )
        self.assertIn(
            "--plan-hash 4da29a1c175b1d5f234644da763e8509c7c02ff6c5e83f5f748346d49a5af725",
            INSTALLER,
        )
        self.assertIn("--hour 3 --minute 0 --daily 7 --weekly 4 --monthly 12", INSTALLER)

    def test_status_activation_installs_repository_credential_and_04_00_policy(self):
        self.assertIn(
            "/etc/tuinstra-backup/restic-passwords/tuinstra-prod-01/status.password",
            INSTALLER,
        )
        self.assertIn(
            "/mnt/hdd1000-01/backups/production/tuinstra-prod-01/status",
            INSTALLER,
        )
        self.assertIn("tuinstra-backup-admin run status", INSTALLER)
        self.assertIn("tuinstra-backup-admin check status", INSTALLER)
        self.assertIn("tuinstra-backup-admin catalog status", INSTALLER)
        self.assertIn("tuinstra-backup-admin retention status", INSTALLER)
        self.assertIn("retain-active --host tuinstra-prod-01 --app status", INSTALLER)
        self.assertIn(
            "--host tuinstra-prod-01 --app status --policy-version production-v1",
            INSTALLER,
        )
        self.assertIn(
            "--plan-hash f3fa9e7a9cee5db9639761c10ad112d867d6c0161c7023b1cd055f5e95563e8d",
            INSTALLER,
        )
        self.assertIn("--hour 4 --minute 0 --daily 7 --weekly 4 --monthly 12", INSTALLER)
        self.assertIn("tuinstra-backup-cycle-tuinstra-prod-01--status.timer", INSTALLER)

    def test_status_has_no_restore_test_permission_without_an_adapter(self):
        self.assertNotIn("tuinstra-backup-admin restore-test status", INSTALLER)

    def test_installer_does_not_start_tracker_or_install_general_sudo(self):
        self.assertNotIn("docker compose", INSTALLER)
        self.assertNotIn("--trigger manual", INSTALLER)
        self.assertNotIn("tuinstra-backup ALL=(root)", INSTALLER)
        self.assertNotIn("tuinstra-backup-admin restore-test *", INSTALLER)
        self.assertIn("tuinstra-backup-admin run tracker", INSTALLER)
        self.assertIn("tuinstra-backup-admin check tracker", INSTALLER)
        self.assertIn("tuinstra-backup-admin restore-test tracker *", INSTALLER)

    def test_retention_service_keeps_umami_and_adds_tracker(self):
        self.assertIn(
            "retain-active --host tuinstra-prod-01 --app umami",
            INSTALLER,
        )
        self.assertIn(
            "retain-active --host tuinstra-prod-02 --app tracker",
            INSTALLER,
        )

    def test_installer_verifies_reviewed_external_inputs_before_mutation(self):
        self.assertIn('install_input_manifest="$bundle_root/install-input/manifest.json"', INSTALLER)
        self.assertIn('python3 "$install_input_verifier" --root "$bundle_root" --manifest "$install_input_manifest"', INSTALLER)
        self.assertLess(INSTALLER.index('python3 "$install_input_verifier"'), INSTALLER.index("apt-get update"))

    def test_manifest_matches_verified_production_hostkeys(self):
        manifest = json.loads(INPUT_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["algorithm"], "sha256")
        self.assertEqual([item["host"] for item in manifest["files"]], ["vps01.tuinstra.dev", "vps02.tuinstra.dev"])
        self.assertEqual(
            [item["fingerprint"] for item in manifest["files"]],
            [
                "SHA256:F/0WJ/wTeXCpYhZWUwfwlt+vQ6/Hnv0bp3v2mpypYxY",
                "SHA256:QKqCwXfnMmKmzicVshD/EebUre5e1NS59ukR2AX+9gY",
            ],
        )

    def test_verifier_accepts_exactly_owned_staged_key(self):
        public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEjqmlxPsPC+r38FrfDZSowv077eLGppVVEy8e2yZvld root@tuinstra-prod-01\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "install-input/hostkeys/vps01.pub"
            target.parent.mkdir(parents=True)
            target.write_text(public_key, encoding="utf-8")
            os.chmod(target, 0o600)
            manifest = json.loads(INPUT_MANIFEST.read_text(encoding="utf-8"))
            manifest["files"] = [dict(manifest["files"][0])]
            manifest["files"][0]["owner"] = pwd.getpwuid(os.getuid()).pw_name
            manifest["files"][0]["group"] = grp.getgrgid(os.getgid()).gr_name
            manifest_path = root / "install-input/manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                ["python3", str(INPUT_VERIFIER), "--root", str(root), "--manifest", str(manifest_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_verifier_rejects_mode_drift(self):
        public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEjqmlxPsPC+r38FrfDZSowv077eLGppVVEy8e2yZvld root@tuinstra-prod-01\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "install-input/hostkeys/vps01.pub"
            target.parent.mkdir(parents=True)
            target.write_text(public_key, encoding="utf-8")
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            manifest = json.loads(INPUT_MANIFEST.read_text(encoding="utf-8"))
            manifest["files"] = [dict(manifest["files"][0])]
            manifest["files"][0]["owner"] = pwd.getpwuid(os.getuid()).pw_name
            manifest["files"][0]["group"] = grp.getgrgid(os.getgid()).gr_name
            manifest_path = root / "install-input/manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = subprocess.run(
                ["python3", str(INPUT_VERIFIER), "--root", str(root), "--manifest", str(manifest_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mode mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
