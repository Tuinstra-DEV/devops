import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
import activate_runner_platform_admission as activation


class FakeCommands:
    def __init__(self, *, domains="", on_virsh=None, fail_start=False):
        self.active = True
        self.domains = domains
        self.on_virsh = on_virsh
        self.fail_start = fail_start
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
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
        self.token = root / "synthetic-token"
        self.state.mkdir()
        self.overlay.mkdir()
        self.token.write_text("synthetic-token-value\n")
        self.token.chmod(0o600)
        source = (Path(__file__).parents[2] / "runner/config/manager.toml").read_text()
        source = source.replace('  "Tuinstra-DEV/wodiq-platform",\n', "")
        source = source.replace(
            '/run/credentials/ci-runner-manager.service/github_token', str(self.token)
        )
        self.base_bytes = source.encode()
        self.config.write_bytes(self.base_bytes)
        self.config.chmod(0o640)

    def tearDown(self):
        self.temp.cleanup()

    def activate(self, commands, opener=None):
        return activation.activate(
            self.config,
            command=commands,
            lock_path=self.lock,
            state_dir=self.state,
            overlay_root=self.overlay,
            opener=opener or FakeOpener(),
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
        self.assertEqual(commands.calls, [])
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
        self.assertTrue(all(
            request.get_header("Authorization") == "Bearer synthetic-token-value"
            for request, _ in opener.requests
        ))

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
