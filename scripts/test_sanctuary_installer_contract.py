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
