"""Offline contracts for DEV-50's narrowly scoped Sanctuary operator installer."""

from importlib.machinery import SourceFileLoader
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest import mock


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts/activate-four-runner-pools.py"
loader = SourceFileLoader("activate_four_runner_pools", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
activation = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = activation
loader.exec_module(activation)


def result(args, output="", code=0):
    return subprocess.CompletedProcess(args, code, output, "")


class FourPoolActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.stage = root / "stage"
        self.stage.mkdir(mode=0o700)
        self.overlay = root / "overlay"
        self.overlay.mkdir(mode=0o700)
        self.state = root / "state"
        self.state.mkdir(mode=0o700)
        self.backup_root = root / "backup"
        self.config = root / "manager.toml"
        self.manager = root / "manager"
        self.helper = root / "helper"
        self.unit = root / "manager.service"
        self.lock = root / "lock"
        self.helper_lock = root / "helper.lock"
        self.dropin = root / "run" / "dev50-four-pool-drain.conf"
        self.layout = activation.Layout(
            config=self.config, manager=self.manager, helper=self.helper,
            unit=self.unit, overlay=self.overlay, state=self.state,
            lock=self.lock, helper_lock=self.helper_lock,
            dropin=self.dropin, backup_root=self.backup_root,
        )
        self.config.write_bytes((ROOT / "runner/config/manager.toml").read_bytes())
        self.manager.write_bytes((ROOT / "runner/manager/ci_runner_manager.py").read_bytes())
        self.helper.write_bytes((ROOT / "runner/host-helper/ci_runner_host_helper.py").read_bytes())
        self.unit.write_bytes((ROOT / "runner/systemd/ci-runner-manager.service").read_bytes())
        for file in (self.config, self.manager, self.helper, self.unit):
            file.chmod(0o640)

    def test_reviewed_binary_hashes_match_this_checkout(self):
        self.assertEqual(activation.digest(self.manager.read_bytes()),
                         activation.MANAGER_SHA256)
        self.assertEqual(activation.digest(self.helper.read_bytes()),
                         activation.HELPER_SHA256)
        self.assertEqual(activation.digest(self.unit.read_bytes()),
                         activation.UNIT_SHA256)

    def test_config_transform_only_changes_approved_pool_fields(self):
        original = self.config.read_bytes()
        old, candidate = activation.validate_legacy_config(original)
        new = tomllib.loads(candidate.decode())
        self.assertEqual(new, {**old, "max_concurrency": 4,
                               **activation.POOL_VALUES})
        self.assertEqual(new["repositories"], old["repositories"])
        self.assertEqual(new["github_token_file"], old["github_token_file"])
        self.assertIn(b'runner_group_id = 1', candidate)
        self.assertIn(b'medium_runner_label = "trusted-medium"', candidate)

    def test_config_rejects_legacy_resource_or_allowlist_drift(self):
        source = self.config.read_bytes()
        cases = (
            source.replace(b"max_concurrency = 1", b"max_concurrency = 2"),
            source.replace(b"runner_memory_mib = 6144", b"runner_memory_mib = 8192"),
            source.replace(b"Tuinstra-DEV/wodiq-platform", b"Other/wodiq-platform"),
            source + b'\npool_mode = "four"\n',
        )
        for invalid in cases:
            with self.subTest(invalid=invalid[-30:]), \
                    self.assertRaises(activation.ActivationError):
                activation.validate_legacy_config(invalid)

    def test_source_hash_rejects_mutation_and_symlink(self):
        for source, filename in ((self.manager, "ci_runner_manager.py"),
                                 (self.helper, "ci_runner_host_helper.py")):
            (self.stage / filename).write_bytes(source.read_bytes())
            (self.stage / filename).chmod(0o644)
        uid = os.getuid()
        original_read = activation.read_regular
        original_dir = activation.checked_directory
        with mock.patch.object(activation, "read_regular",
                               side_effect=lambda path, **_: original_read(path, owner=uid)), \
                mock.patch.object(activation, "checked_directory",
                                  side_effect=lambda path, **_: original_dir(path, owner=uid)):
            self.assertEqual(set(activation.source_payloads(self.stage, self.layout)),
                             {"manager", "helper"})
            staged = self.stage / "ci_runner_manager.py"
            staged.write_bytes(staged.read_bytes() + b"# tampered\n")
            with self.assertRaisesRegex(activation.ActivationError, "SHA-256"):
                activation.source_payloads(self.stage, self.layout)
            staged.unlink()
            staged.symlink_to(self.manager)
            with self.assertRaisesRegex(activation.ActivationError, "unsafe owner"):
                activation.source_payloads(self.stage, self.layout)

    def test_drain_refuses_domain_pending_cleanup_and_orphan(self):
        with mock.patch.object(activation, "state_owner", return_value=os.getuid()), \
                mock.patch.object(activation, "checked_directory"):
            domain = lambda *_args, **_kwargs: result([], "sanctuary-ci-gh-123\n")
            with self.assertRaisesRegex(activation.ActivationError, "VM remains"):
                activation.assert_drained(self.layout, domain)
            empty = lambda *_args, **_kwargs: result([], "")
            (self.state / "cleanup-pending.json").write_text("{}")
            with self.assertRaisesRegex(activation.ActivationError, "cleanup obligation"):
                activation.assert_drained(self.layout, empty)
            (self.state / "cleanup-pending.json").unlink()
            (self.overlay / "orphan").mkdir()
            with self.assertRaisesRegex(activation.ActivationError, "orphan"):
                activation.assert_drained(self.layout, empty)

    def test_runtime_drain_uses_reconcile_without_dispatch_and_removes_on_timeout(self):
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            if args[:1] == ["busctl"]:
                return result(args, json.dumps({"type": "a(ss)",
                                                "data": [["github_token", "/etc/ci-runner/github.token"]]}))
            if args[-1] == "--value":
                if "--property=Type" in args:
                    return result(args, "oneshot\n")
                if "--property=Restart" in args:
                    return result(args, "no\n")
                return result(args,
                              "{ path=/usr/local/bin/ci-runner-manager ; argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml reconcile ; }\n")
            return result(args)

        with mock.patch.object(activation, "service_active", side_effect=[True, False]), \
                mock.patch.object(activation, "activate_dropin", return_value=(1, 2)), \
                mock.patch.object(activation, "remove_dropin") as remove, \
                mock.patch.object(activation, "assert_drained",
                                  side_effect=activation.ActivationError("still running")), \
                mock.patch.object(activation.time, "monotonic", side_effect=[0, 1, 2]):
            with self.assertRaisesRegex(activation.ActivationError, "deadline"):
                activation.drain_existing_jobs(self.layout, 1, command, pause=lambda _: None)
        self.assertIn(["systemctl", "start", activation.SERVICE], calls)
        self.assertNotIn(["systemctl", "stop", "sanctuary-ci-gh-123"], calls)
        self.assertTrue(all("daemon" not in " ".join(call)
                            for call in calls if call[:2] == ["systemctl", "start"]))
        remove.assert_called_once_with(self.layout, (1, 2), command)

    def test_activation_timeout_restarts_original_manager_without_killing_vm(self):
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            return result(args)

        with mock.patch.object(activation, "preflight", return_value=({}, {}, {})), \
                mock.patch.object(activation, "lock_file", side_effect=lambda _: os.open(self.lock, os.O_CREAT | os.O_RDWR, 0o600)), \
                mock.patch.object(activation, "drain_existing_jobs",
                                  side_effect=activation.ActivationError("deadline")), \
                mock.patch.object(activation, "verify_base_unit"), \
                mock.patch.object(activation, "service_active", return_value=True):
            with self.assertRaisesRegex(activation.ActivationError, "original runner policy restored"):
                activation.activate(self.stage, 60, self.layout, command)
        self.assertIn(["systemctl", "start", activation.SERVICE], calls)
        self.assertFalse(any(call[:2] == ["virsh", "destroy"] for call in calls))

    def test_ambiguous_dropin_state_blocks_claimed_daemon_recovery(self):
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            return result(args)

        with mock.patch.object(activation, "preflight", return_value=({}, {}, {})), \
                mock.patch.object(activation, "lock_file",
                                  side_effect=lambda _: os.open(self.lock, os.O_CREAT | os.O_RDWR, 0o600)), \
                mock.patch.object(activation, "drain_existing_jobs",
                                  side_effect=activation.ActivationError("drop-in reload uncertain")), \
                mock.patch.object(activation, "verify_base_unit",
                                  side_effect=activation.ActivationError("effective Type still oneshot")):
            with self.assertRaisesRegex(activation.ActivationError,
                                        "safe automatic rollback could not be verified"):
                activation.activate(self.stage, 60, self.layout, command)
        self.assertNotIn(["systemctl", "start", activation.SERVICE], calls)

    def test_rollback_with_live_vm_does_not_stop_admission_or_guest(self):
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            if args[:1] == ["virsh"]:
                return result(args, "sanctuary-ci-gh-123\n")
            return result(args)

        with mock.patch.object(activation, "lock_file",
                               side_effect=lambda _: os.open(self.lock, os.O_CREAT | os.O_RDWR, 0o600)):
            with self.assertRaisesRegex(activation.ActivationError, "VM remains"):
                activation.rollback("dev50-20261007T120000Z-abcdef12", self.layout, command)
        self.assertFalse(any(call[:2] == ["systemctl", "stop"] for call in calls))
        self.assertFalse(any(call[:2] == ["virsh", "destroy"] for call in calls))

    def test_canary_is_no_jit_and_uses_helper_equivalent_sandbox(self):
        calls = []
        activation.storage_canary(lambda args, **_kwargs: calls.append(args) or result(args))
        command = calls[0]
        self.assertIn("--property=ProtectSystem=strict", command)
        self.assertIn("--property=ReadWritePaths=/var/lib/ci-runner/overlay /run/lock", command)
        self.assertIn("fstrim", activation.CANARY_CODE)
        self.assertIn("create_bounded_storage", activation.CANARY_CODE)
        self.assertIn("remove_bounded_storage", activation.CANARY_CODE)
        self.assertNotIn("launch(", activation.CANARY_CODE)

    def test_failed_canary_reports_only_allowlisted_journal_stage(self):
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            if args[0] == "systemd-run":
                return result(args, code=1)
            if args[:2] == ["systemctl", "stop"]:
                return result(args)
            if args[:2] == ["systemctl", "show"]:
                return result(args, "LoadState=not-found\nActiveState=inactive\n"
                              "MainPID=0\nControlPID=0\n")
            if args[0] == "journalctl":
                return result(args, "private traceback ignored\n"
                              "DEV50_CANARY stage=create error=mount_namespace_mismatch\n")
            raise AssertionError(args)

        with self.assertRaises(activation.ActivationError) as caught:
            activation.storage_canary(command)
        message = str(caught.exception)
        self.assertIn("ci-runner-dev50-canary-", message)
        self.assertIn("exit=1", message)
        self.assertIn("stage=create", message)
        self.assertIn("error=mount_namespace_mismatch", message)
        self.assertNotIn("private traceback", message)
        self.assertNotIn("--pipe", calls[0])
        self.assertIn("--property=StandardError=journal", calls[0])
        self.assertIn("--property=RuntimeMaxSec=240", calls[0])
        self.assertEqual([call[:2] for call in calls[1:3]],
                         [["systemctl", "stop"], ["systemctl", "show"]])
        self.assertTrue(any(call[0] == "journalctl" for call in calls))

    def test_failed_canary_does_not_echo_unknown_journal_text(self):
        def command(args, **_kwargs):
            if args[0] == "systemd-run":
                return result(args, code=1)
            if args[:2] == ["systemctl", "stop"]:
                return result(args)
            if args[:2] == ["systemctl", "show"]:
                return result(args, "LoadState=not-found\nActiveState=inactive\n"
                              "MainPID=0\nControlPID=0\n")
            return result(args, "credential-like arbitrary output\n"
                          "DEV50_CANARY stage=bad error=unapproved\n")

        with self.assertRaises(activation.ActivationError) as caught:
            activation.storage_canary(command)
        message = str(caught.exception)
        self.assertIn("stage=unknown", message)
        self.assertIn("error=unknown", message)
        self.assertNotIn("credential-like", message)
        self.assertNotIn("unapproved", message)

    def test_failed_canary_retains_unit_and_exit_when_journal_is_unavailable(self):
        def command(args, **_kwargs):
            if args[0] == "systemd-run":
                return result(args, code=1)
            if args[:2] == ["systemctl", "stop"]:
                return result(args)
            if args[:2] == ["systemctl", "show"]:
                return result(args, "LoadState=not-found\nActiveState=inactive\n"
                              "MainPID=0\nControlPID=0\n")
            raise activation.ActivationError("journal unavailable")

        with self.assertRaises(activation.ActivationError) as caught:
            activation.storage_canary(command)
        message = str(caught.exception)
        self.assertIn("ci-runner-dev50-canary-", message)
        self.assertIn("exit=1", message)
        self.assertIn("stage=unknown error=unknown", message)
        self.assertNotIn("journal unavailable", message)

    def test_launcher_timeout_stops_and_verifies_exact_canary_unit(self):
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            if args[0] == "systemd-run":
                raise activation.ActivationError("launcher timeout")
            if args[:2] == ["systemctl", "stop"]:
                return result(args)
            if args[:2] == ["systemctl", "show"]:
                return result(args, "LoadState=not-found\nActiveState=inactive\n"
                              "MainPID=0\nControlPID=0\n")
            raise AssertionError(args)

        with self.assertRaises(activation.ActivationError) as caught:
            activation.storage_canary(command)
        self.assertNotIsInstance(caught.exception, activation.CanaryIndeterminate)
        self.assertIn("exit=unknown", str(caught.exception))
        exact_unit = next(arg for arg in calls[0] if arg.startswith("--unit="))[7:] + ".service"
        self.assertEqual(calls[1], ["systemctl", "stop", exact_unit])
        self.assertEqual(calls[2][2], exact_unit)

    def test_canary_stop_status_with_live_pid_is_indeterminate(self):
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            if args[0] == "systemd-run":
                return result(args, code=1)
            if args[:2] == ["systemctl", "stop"]:
                return result(args)
            if args[:2] == ["systemctl", "show"]:
                return result(args, "LoadState=loaded\nActiveState=deactivating\n"
                              "MainPID=321\nControlPID=0\n")
            raise AssertionError(args)

        with self.assertRaises(activation.CanaryIndeterminate) as caught:
            activation.storage_canary(command)
        self.assertIn("stop_status=unverified", str(caught.exception))
        self.assertFalse(any(call[0] == "journalctl" for call in calls))

    def test_canary_status_uncertain_keeps_installed_files_and_admission_stopped(self):
        original = {"config": self.config.read_bytes(), "manager": self.manager.read_bytes(),
                    "helper": self.helper.read_bytes()}
        metadata = {key: path.stat() for key, path in
                    (("config", self.config), ("manager", self.manager),
                     ("helper", self.helper))}
        replacement = {"config": activation.validate_legacy_config(original["config"])[1],
                       "manager": original["manager"] + b"# candidate\n",
                       "helper": original["helper"] + b"# candidate\n"}
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            if args[:3] == ["systemctl", "is-active", "--quiet"]:
                return result(args, code=3)
            return result(args)

        real_read = activation.read_regular
        real_directory = activation.checked_directory
        uid = os.getuid()
        with mock.patch.object(activation, "preflight",
                               return_value=(replacement, original, metadata)), \
                mock.patch.object(activation, "read_regular",
                                  side_effect=lambda path, **_: real_read(path, owner=uid)), \
                mock.patch.object(activation, "checked_directory",
                                  side_effect=lambda path, **_: real_directory(path, owner=uid)), \
                mock.patch.object(activation, "state_owner", return_value=uid), \
                mock.patch.object(activation, "lock_file",
                                  side_effect=lambda path: os.open(path, os.O_CREAT | os.O_RDWR, 0o600)), \
                mock.patch.object(activation.os, "fchown"), \
                mock.patch.object(activation, "drain_existing_jobs", return_value=(1, 2)), \
                mock.patch.object(activation, "assert_no_helper_instances"), \
                mock.patch.object(activation, "storage_canary",
                                  side_effect=activation.CanaryIndeterminate("canary unit=ci-runner-dev50-canary-test status=unknown")), \
                mock.patch.object(activation, "remove_dropin") as remove_dropin, \
                mock.patch.object(activation, "restore_files") as restore:
            with self.assertRaises(activation.CanaryIndeterminate) as caught:
                activation.activate(self.stage, 60, self.layout, command)
        self.assertIn("admission remains stopped", str(caught.exception))
        self.assertEqual(self.config.read_bytes(), replacement["config"])
        self.assertFalse(any(call[:2] == ["systemctl", "start"] for call in calls))
        restore.assert_not_called()
        remove_dropin.assert_not_called()

    def test_stopping_helper_instance_still_blocks_install(self):
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            return result(args, "ci-runner-host-helper@12.service loaded deactivating stop-sigterm\n")

        with self.assertRaisesRegex(activation.ActivationError, "instance remains active"):
            activation.assert_no_helper_instances(command)
        self.assertIn("--state=activating,running,reloading,deactivating", calls[0])

    def test_restore_refuses_unrecognized_current_file_before_any_write(self):
        backup_id = "dev50-20261007T120000Z-abcdef12"
        backup = self.backup_root / backup_id
        backup.mkdir(parents=True)
        content = {"config": self.config.read_bytes(),
                   "manager": self.manager.read_bytes(), "helper": self.helper.read_bytes()}
        manifest = {key: {"old_sha256": activation.digest(data),
                          "new_sha256": activation.digest(data + b"new"),
                          "uid": os.getuid(), "gid": os.getgid(), "mode": 0o640}
                    for key, data in content.items()}
        for key, data in content.items():
            (backup / key).write_bytes(data)
        (backup / "manifest.json").write_text(json.dumps(manifest))
        self.manager.write_bytes(b"unrelated operator change")
        uid = os.getuid()
        original_read = activation.read_regular
        original_dir = activation.checked_directory
        with mock.patch.object(activation, "read_regular",
                               side_effect=lambda path, **_: original_read(path, owner=uid)), \
                mock.patch.object(activation, "checked_directory",
                                  side_effect=lambda path, **_: original_dir(path, owner=uid)), \
                mock.patch.object(activation, "atomic_write") as write:
            with self.assertRaisesRegex(activation.ActivationError, "does not match"):
                activation.restore_files(self.layout, backup, allow_partial=False)
            write.assert_not_called()

    def run_transaction_failure(self, *, orphan=False, fail_install=False):
        original = {"config": self.config.read_bytes(),
                    "manager": self.manager.read_bytes(), "helper": self.helper.read_bytes()}
        metadata = {key: path.stat() for key, path in
                    (("config", self.config), ("manager", self.manager),
                     ("helper", self.helper))}
        replacement = {"config": activation.validate_legacy_config(original["config"])[1],
                       "manager": original["manager"] + b"# reviewed candidate\n",
                       "helper": original["helper"] + b"# reviewed candidate\n"}
        services = {activation.SERVICE: True, activation.SOCKET: True}
        calls = []

        def command(args, **_kwargs):
            calls.append(args)
            if args[:2] == ["systemctl", "stop"]:
                services[args[2]] = False
            if args[:2] == ["systemctl", "start"]:
                services[args[2]] = True
            if args[:3] == ["systemctl", "is-active", "--quiet"]:
                return result(args, code=0 if services[args[3]] else 3)
            return result(args)

        def fail_canary(_command):
            if orphan:
                (self.overlay / "retained-canary").mkdir()
            raise activation.ActivationError("canary stage=create error=mount_namespace_mismatch")

        real_read = activation.read_regular
        real_directory = activation.checked_directory
        real_atomic = activation.atomic_write
        uid = os.getuid()
        install_failed = False

        def atomic(path, data, **kwargs):
            nonlocal install_failed
            if fail_install and path == self.config and not install_failed:
                install_failed = True
                raise OSError("synthetic atomic replacement failure")
            return real_atomic(path, data, **kwargs)

        with mock.patch.object(activation, "preflight",
                               return_value=(replacement, original, metadata)), \
                mock.patch.object(activation, "read_regular",
                                  side_effect=lambda path, **_: real_read(path, owner=uid)), \
                mock.patch.object(activation, "checked_directory",
                                  side_effect=lambda path, **_: real_directory(path, owner=uid)), \
                mock.patch.object(activation, "state_owner", return_value=uid), \
                mock.patch.object(activation, "lock_file",
                                  side_effect=lambda path: os.open(path, os.O_CREAT | os.O_RDWR, 0o600)), \
                mock.patch.object(activation.os, "fchown"), \
                mock.patch.object(activation, "drain_existing_jobs", return_value=(1, 2)), \
                mock.patch.object(activation, "remove_dropin") as remove_dropin, \
                mock.patch.object(activation, "verify_base_unit"), \
                mock.patch.object(activation, "storage_canary", side_effect=fail_canary) as canary, \
                mock.patch.object(activation, "atomic_write", side_effect=atomic):
            if orphan:
                expected = "safe automatic rollback could not be verified"
            else:
                expected = "original runner policy restored"
            with self.assertRaisesRegex(activation.ActivationError, expected) as caught:
                activation.activate(self.stage, 60, self.layout, command)
            if not fail_install:
                self.assertIn("mount_namespace_mismatch", str(caught.exception))

        self.assertFalse(any(call[:2] == ["virsh", "destroy"] for call in calls))
        if orphan:
            self.assertEqual(self.config.read_bytes(), replacement["config"])
            self.assertFalse(services[activation.SERVICE])
            self.assertFalse(services[activation.SOCKET])
            remove_dropin.assert_not_called()
        else:
            self.assertEqual(self.config.read_bytes(), original["config"])
            self.assertEqual(self.manager.read_bytes(), original["manager"])
            self.assertEqual(self.helper.read_bytes(), original["helper"])
            self.assertTrue(services[activation.SERVICE])
            self.assertTrue(services[activation.SOCKET])
            remove_dropin.assert_called_once()
        if fail_install:
            canary.assert_not_called()
        else:
            canary.assert_called_once()

    def test_clean_canary_failure_restores_three_files_and_daemon(self):
        self.run_transaction_failure()

    def test_retained_canary_storage_keeps_admission_stopped(self):
        self.run_transaction_failure(orphan=True)

    def test_partial_file_install_restores_all_original_files(self):
        self.run_transaction_failure(fail_install=True)


if __name__ == "__main__":
    unittest.main()
