import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
import activate_runner_platform_admission as activation


class FakeCommands:
    def __init__(self, *, domains="", on_virsh=None, fail_start=False,
                 credential_binding=("github_token", "/etc/ci-runner/github.token")):
        self.active = True
        self.domains = domains
        self.on_virsh = on_virsh
        self.fail_start = fail_start
        self.credential_binding = credential_binding
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[:2] == ["busctl", "--json=short"]:
            stdout = json.dumps({"type": "a(ss)", "data": [list(self.credential_binding)]}).encode()
            return subprocess.CompletedProcess(argv, 0, stdout, b"")
        if argv[:2] == ["systemctl", "is-active"]:
            stdout = b"active\n" if self.active else b"inactive\n"
            return subprocess.CompletedProcess(argv, 0 if self.active else 3, stdout, b"")
        if argv[:2] == ["systemctl", "stop"]:
            self.active = False
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[:2] == ["systemctl", "start"]:
            if self.fail_start:
                return subprocess.CompletedProcess(argv, 1, b"", b"failure hidden")
            self.active = True
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[:1] == ["virsh"]:
            if self.on_virsh is not None:
                self.on_virsh()
            return subprocess.CompletedProcess(argv, 0, self.domains.encode(), b"")
        raise AssertionError(f"unexpected host command: {argv[0]}")


class FakeResponse:
    status = 200

    def close(self):
        pass


class FakeOpener:
    def __init__(self, statuses=None):
        self.statuses = list(statuses or [200, 200])
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        status = self.statuses.pop(0)
        if status != 200:
            raise activation.urllib.error.HTTPError(
                request.full_url, status, "private fixture", {}, io.BytesIO()
            )
        return FakeResponse()


class RunnerAdmissionActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = root / "manager.toml"
        self.lock = root / "admission.lock"
        self.state = root / "state"
        self.overlay = root / "overlay"
        self.credentials = root / "credentials"
        self.credentials.mkdir(mode=0o700)
        self.token = self.credentials / "github_token"
        self.state.mkdir()
        self.overlay.mkdir()
        self.token.write_text("synthetic-token-value\n")
        self.token.chmod(0o440)
        self.credentials.chmod(0o550)
        source = (Path(__file__).parents[2] / "runner/config/manager.toml").read_text()
        source = source.replace('  "Tuinstra-DEV/wodiq-platform",\n', "")
        self.base_bytes = source.encode()
        self.config.write_bytes(self.base_bytes)
        self.config.chmod(0o640)

    def tearDown(self):
        self.credentials.chmod(0o700)
        self.temp.cleanup()

    def activate(self, commands, opener=None):
        return activation.activate(
            self.config,
            command=commands,
            lock_path=self.lock,
            state_dir=self.state,
            overlay_root=self.overlay,
            opener=opener or FakeOpener(),
            credential_file=self.token,
            credential_directory=self.credentials,
            credential_owner=(os.geteuid(), os.getegid()),
        )

    def test_happy_path_only_adds_repository_and_keeps_backup_metadata(self):
        commands = FakeCommands()
        original_stat = self.config.stat()
        result = self.activate(commands)

        installed = self.config.read_bytes()
        self.assertEqual(
            installed,
            self.base_bytes.replace(
                b'  "Tuinstra-DEV/wodiq-app",\n',
                b'  "Tuinstra-DEV/wodiq-app",\n  "Tuinstra-DEV/wodiq-platform",\n',
            ),
        )
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.config.stat().st_uid, original_stat.st_uid)
        self.assertEqual(self.config.stat().st_gid, original_stat.st_gid)
        self.assertIn("backup_sha256=", result)
        backup = Path(result.split("backup=", 1)[1].split(";", 1)[0])
        self.assertEqual(backup.read_bytes(), self.base_bytes)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o640)
        self.assertIn(["systemctl", "stop", activation.SERVICE], commands.calls)
        self.assertIn(["systemctl", "start", activation.SERVICE], commands.calls)
        self.assertTrue(commands.active)

    def test_idempotent_activation_does_not_restart_or_rewrite(self):
        admitted = self.base_bytes.replace(
            b'  "Tuinstra-DEV/wodiq-app",\n',
            b'  "Tuinstra-DEV/wodiq-app",\n  "Tuinstra-DEV/wodiq-platform",\n',
        )
        self.config.write_bytes(admitted)
        before = self.config.stat()
        commands = FakeCommands()
        result = self.activate(commands)
        after = self.config.stat()
        self.assertIn("already in the runner allowlist", result)
        self.assertEqual(self.config.read_bytes(), admitted)
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(commands.calls, [])

    def test_missing_required_config_field_fails_before_host_mutation(self):
        self.config.write_bytes(self.base_bytes.replace(b"max_concurrency = 1\n", b""))
        commands = FakeCommands()
        with self.assertRaisesRegex(activation.ActivationError, "missing required fields"):
            self.activate(commands)
        self.assertEqual(commands.calls, [])

    def test_duplicate_repository_fails_before_host_mutation(self):
        duplicate = self.base_bytes.replace(
            b'  "Tuinstra-DEV/wodiq-app",\n',
            b'  "Tuinstra-DEV/wodiq-app",\n  "Tuinstra-DEV/wodiq-app",\n',
        )
        self.config.write_bytes(duplicate)
        commands = FakeCommands()
        with self.assertRaisesRegex(activation.ActivationError, "duplicates"):
            self.activate(commands)
        self.assertEqual(commands.calls, [])

    def test_unexpected_allowlist_fails_before_host_mutation(self):
        unexpected = self.base_bytes.replace(
            b'  "Tuinstra-DEV/tracker",\n',
            b'  "Tuinstra-DEV/openairco",\n  "Tuinstra-DEV/tracker",\n',
        )
        self.config.write_bytes(unexpected)
        commands = FakeCommands()
        with self.assertRaisesRegex(activation.ActivationError, "differs from the reviewed set"):
            self.activate(commands)
        self.assertEqual(commands.calls, [])

    def test_missing_repository_permission_blocks_before_stopping_runner(self):
        commands = FakeCommands()
        opener = FakeOpener([200, 403])
        with self.assertRaisesRegex(activation.ActivationError, "HTTP 403.*configuration unchanged") as raised:
            self.activate(commands, opener)
        self.assertNotIn("synthetic-token-value", str(raised.exception))
        self.assertEqual(commands.calls[0][:2], ["busctl", "--json=short"])
        self.assertFalse(any(call[:2] == ["systemctl", "stop"] for call in commands.calls))
        self.assertEqual(self.config.read_bytes(), self.base_bytes)
        self.assertFalse(list(self.config.parent.glob("manager.toml.bak.*")))

    def test_repository_access_checks_cover_actions_and_runner_admin(self):
        commands = FakeCommands()
        opener = FakeOpener()
        self.activate(commands, opener)
        self.assertEqual(len(opener.requests), 2)
        self.assertIn("/actions/runs?status=queued", opener.requests[0][0].full_url)
        self.assertIn("/actions/runners", opener.requests[1][0].full_url)
        self.assertEqual(opener.requests[0][1], 15)
        self.assertEqual(commands.calls[0], [
            "busctl", "--json=short", "get-property", "org.freedesktop.systemd1",
            activation.SYSTEMD_UNIT_OBJECT, "org.freedesktop.systemd1.Service", "LoadCredential",
        ])
        self.assertTrue(all(
            request.get_header("Authorization") == "Bearer synthetic-token-value"
            for request, _ in opener.requests
        ))

    def test_systemd_readonly_credential_accepts_modes_inside_private_runtime_directory(self):
        # Read bits are accepted only while the exact unit-private directory
        # and root-owned group keep the credential inaccessible to others.
        for mode in (0o400, 0o440, 0o444):
            with self.subTest(mode=oct(mode)):
                self.token.chmod(mode)
                result = self.activate(FakeCommands())
                self.assertIn("WODIQ Platform admitted", result)
                self.config.write_bytes(self.base_bytes)

    def test_systemd_readonly_credential_rejects_other_accessible_runtime_directory(self):
        self.credentials.chmod(0o755)
        commands = FakeCommands()
        with self.assertRaisesRegex(activation.ActivationError, "directory violates"):
            self.activate(commands)
        self.assertFalse(any(call[:2] == ["systemctl", "stop"] for call in commands.calls))

    def test_systemd_credential_rejects_unexpected_effective_unit_binding(self):
        commands = FakeCommands(credential_binding=("other", "/etc/other.token"))
        with self.assertRaisesRegex(activation.ActivationError, "binding differs"):
            self.activate(commands)
        self.assertFalse(any(call[:2] == ["systemctl", "stop"] for call in commands.calls))
        self.assertEqual(self.config.read_bytes(), self.base_bytes)

    def test_systemd_credential_rejects_symlink(self):
        self.credentials.chmod(0o700)
        self.token.unlink()
        secret = self.credentials / "other"
        secret.write_text("synthetic-token-value\n")
        secret.chmod(0o440)
        self.token.symlink_to(secret)
        commands = FakeCommands()
        with self.assertRaises(activation.ActivationError):
            self.activate(commands)
        self.assertFalse(any(call[:2] == ["systemctl", "stop"] for call in commands.calls))

    def test_systemd_credential_rejects_wrong_file_uid_or_gid(self):
        expected = (os.geteuid(), os.getegid())
        original_fstat = os.fstat
        for field, value in ((4, expected[0] + 1), (5, expected[1] + 1)):
            with self.subTest(field=field):
                def changed_owner(fd, *, _field=field, _value=value):
                    current = original_fstat(fd)
                    values = list(current)
                    values[_field] = _value
                    return os.stat_result(values)

                with mock.patch.object(activation.os, "fstat", side_effect=changed_owner):
                    with self.assertRaisesRegex(activation.ActivationError, "file violates"):
                        activation._read_systemd_credential(
                            self.token,
                            credential_directory=self.credentials,
                            expected_owner=expected,
                        )

    def test_systemd_credential_rejects_nonreadable_or_executable_modes(self):
        for mode in (0o000, 0o111, 0o555):
            with self.subTest(mode=oct(mode)):
                self.token.chmod(mode)
                commands = FakeCommands()
                with self.assertRaisesRegex(activation.ActivationError, "file violates"):
                    self.activate(commands)
                self.assertFalse(any(call[:2] == ["systemctl", "stop"] for call in commands.calls))

    def test_systemd_credential_rejects_writable_parent_and_file(self):
        self.credentials.chmod(0o770)
        commands = FakeCommands()
        with self.assertRaisesRegex(activation.ActivationError, "directory violates"):
            self.activate(commands)
        self.assertFalse(any(call[:2] == ["systemctl", "stop"] for call in commands.calls))
        self.credentials.chmod(0o700)

        self.token.chmod(0o600)
        commands = FakeCommands()
        with self.assertRaisesRegex(activation.ActivationError, "file violates"):
            self.activate(commands)
        self.assertFalse(any(call[:2] == ["systemctl", "stop"] for call in commands.calls))

    def test_manager_config_rejects_noncanonical_credential_path(self):
        self.config.write_bytes(self.base_bytes.replace(
            b'/run/credentials/ci-runner-manager.service/github_token',
            b'/etc/ci-runner/github.token',
        ))
        commands = FakeCommands()
        with self.assertRaisesRegex(activation.ActivationError, "differs from the reviewed"):
            self.activate(commands)
        self.assertEqual(commands.calls, [])

    def test_active_sanctuary_domain_refuses_change_and_restarts_manager(self):
        commands = FakeCommands(domains="sanctuary-ci-active-job\n")
        with self.assertRaisesRegex(activation.ActivationError, "domain is active"):
            self.activate(commands)
        self.assertEqual(self.config.read_bytes(), self.base_bytes)
        self.assertTrue(commands.active)
        self.assertFalse(list(self.config.parent.glob("manager.toml.bak.*")))

    def test_pending_lease_refuses_change_and_restarts_manager(self):
        (self.state / "lease-active.json").write_text("{}\n")
        commands = FakeCommands()
        with self.assertRaisesRegex(activation.ActivationError, "active lease or pending cleanup"):
            self.activate(commands)
        self.assertEqual(self.config.read_bytes(), self.base_bytes)
        self.assertTrue(commands.active)

    def test_pending_cleanup_refuses_change_and_restarts_manager(self):
        (self.state / "cleanup-pending.json").write_text("{}\n")
        commands = FakeCommands()
        with self.assertRaisesRegex(activation.ActivationError, "active lease or pending cleanup"):
            self.activate(commands)
        self.assertEqual(self.config.read_bytes(), self.base_bytes)
        self.assertTrue(commands.active)

    def test_source_change_during_drain_refuses_overwrite(self):
        changed = self.base_bytes + b"\n# concurrent administrator change\n"
        commands = FakeCommands(on_virsh=lambda: self.config.write_bytes(changed))
        with self.assertRaisesRegex(activation.ActivationError, "changed during preflight"):
            self.activate(commands)
        self.assertEqual(self.config.read_bytes(), changed)
        self.assertTrue(commands.active)
        self.assertFalse(list(self.config.parent.glob("manager.toml.bak.*")))

    def test_failed_restart_restores_original_config_and_starts_old_manager(self):
        class FailOnce(FakeCommands):
            def __init__(self):
                super().__init__()
                self.starts = 0

            def __call__(self, argv, **kwargs):
                if argv[:2] == ["systemctl", "start"]:
                    self.starts += 1
                    if self.starts == 1:
                        self.calls.append(list(argv))
                        return subprocess.CompletedProcess(argv, 1, b"", b"hidden")
                return super().__call__(argv, **kwargs)

        commands = FailOnce()
        with self.assertRaisesRegex(activation.ActivationError, "host command failed"):
            self.activate(commands)
        self.assertEqual(self.config.read_bytes(), self.base_bytes)
        self.assertTrue(commands.active)

    def test_rollback_requires_exact_reviewed_current_and_backup(self):
        commands = FakeCommands()
        result = self.activate(commands)
        backup = Path(result.split("backup=", 1)[1].split(";", 1)[0])
        rollback_commands = FakeCommands()
        message = activation.rollback(
            self.config,
            backup,
            command=rollback_commands,
            lock_path=self.lock,
            state_dir=self.state,
            overlay_root=self.overlay,
        )
        self.assertEqual(self.config.read_bytes(), self.base_bytes)
        self.assertIn("reverse_backup_sha256=", message)
        self.assertTrue(rollback_commands.active)


if __name__ == "__main__":
    unittest.main()
