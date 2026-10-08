"""Failure and preservation contracts for the pinned root recovery operator."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/recover-loop-identity.py"
spec = importlib.util.spec_from_file_location("recover_loop_identity", SCRIPT)
recovery = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = recovery
spec.loader.exec_module(recovery)


class FakeServices:
    def __init__(self):
        self.manager_active = True
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(argv)
        if argv[:2] == ["systemctl", "stop"]:
            self.manager_active = False
        elif argv[:2] == ["systemctl", "start"]:
            self.manager_active = True
        else:
            raise AssertionError(f"unexpected command {argv}")
        return subprocess.CompletedProcess(argv, 0, "", "")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / "stage"
        self.stage.mkdir(mode=0o700)
        self.layout = recovery.Layout(
            manager=self.root / "manager", helper=self.root / "helper",
            config=self.root / "config", token=self.root / "token",
            state=self.root / "state", runtime=self.root / "runtime",
            manager_lock=self.root / "runtime/manager.lock",
            helper_lock=self.root / "helper.lock",
            operator_lock=self.root / "operator.lock",
            backup_root=self.root / "backup", overlay=self.root / "overlay",
            qemu_logs=self.root / "qemu-logs",
        )
        self.layout.helper.write_bytes(b"old-helper")
        self.layout.helper.chmod(0o755)
        self.services = FakeServices()
        self.payloads = []

    def fake_write(self, path, data, *_args):
        self.assertEqual(path, self.layout.helper)
        self.payloads.append(data)
        path.write_bytes(data)

    def common_patches(self):
        state = lambda name, _cmd: (
            ("active", 0, 0) if name == recovery.SOCKET else
            ("active", 1, 0) if self.services.manager_active else ("inactive", 0, 0)
        )
        return (
            mock.patch.object(recovery.os, "geteuid", return_value=0),
            mock.patch.object(recovery, "checked_bytes", return_value=(b"new-helper", b"old-helper", self.layout.helper.stat())),
            mock.patch.object(recovery, "load_manager", return_value=(object(), {})),
            mock.patch.object(recovery, "locked_file", side_effect=lambda *_: os.open(os.devnull, os.O_RDONLY)),
            mock.patch.object(recovery, "runtime_lock", side_effect=lambda *_: os.open(os.devnull, os.O_RDONLY)),
            mock.patch.object(recovery, "service_state", side_effect=state),
            mock.patch.object(recovery, "no_active_helper_units"),
            mock.patch.object(recovery, "regular", return_value=(b"old-helper", self.layout.helper.stat())),
            mock.patch.object(recovery, "OLD_HELPER_SHA256", recovery.digest(b"old-helper")),
            mock.patch.object(recovery, "backup_old", return_value=self.root / "backup"),
            mock.patch.object(recovery, "atomic_write", side_effect=self.fake_write),
        )

    def test_socket_status_has_no_main_pid(self):
        reply = subprocess.CompletedProcess([], 0, "ControlPID=0\nActiveState=active\n", "")
        self.assertEqual(recovery.service_state(recovery.SOCKET, lambda *_: reply), ("active", 0, 0))

    def test_service_missing_main_pid_still_fails_closed(self):
        reply = subprocess.CompletedProcess([], 0, "ControlPID=0\nActiveState=active\n", "")
        with self.assertRaises(recovery.RecoveryError):
            recovery.service_state(recovery.SERVICE, lambda *_: reply)

    def test_wrong_hash_rejected_before_any_service_change(self):
        with mock.patch.object(recovery, "directory"), \
             mock.patch.object(recovery, "regular", side_effect=[
                 (b"print('wrong')", None), (b"old", None), (b"manager", None)
             ]), self.assertRaisesRegex(recovery.RecoveryError, "hash mismatch"):
            recovery.checked_bytes(self.stage, self.layout)
        self.assertEqual(self.services.calls, [])

    def test_second_operator_race_is_rejected_before_manager_stop(self):
        checked = mock.Mock(side_effect=[
            (b"new-helper", b"old-helper", self.layout.helper.stat()),
            recovery.RecoveryError("pinned helper or live manager hash mismatch"),
        ])
        with mock.patch.object(recovery.os, "geteuid", return_value=0), \
             mock.patch.object(recovery, "checked_bytes", checked), \
             mock.patch.object(recovery, "load_manager", return_value=(object(), {})) as load, \
             mock.patch.object(recovery, "locked_file", side_effect=lambda *_: os.open(os.devnull, os.O_RDONLY)), \
             mock.patch.object(recovery, "service_state") as service_state, \
             self.assertRaisesRegex(recovery.RecoveryError, "hash mismatch"):
            recovery.repair(self.stage, layout=self.layout, command=self.services)
        self.assertEqual(checked.call_count, 2)
        self.assertEqual(load.call_count, 1)
        service_state.assert_not_called()
        self.assertEqual(self.services.calls, [])

    def test_socket_probe_failure_restores_old_helper_and_keeps_manager_stopped(self):
        with self._patched_common(), self.assertRaisesRegex(recovery.RecoveryError, "old helper restored"):
            recovery.repair(
                self.stage, layout=self.layout, command=self.services,
                probe=lambda *_: (_ for _ in ()).throw(RuntimeError("socket unavailable")),
                capture=lambda *_: {},
            )
        self.assertEqual(self.payloads, [b"new-helper", b"old-helper"])
        self.assertEqual(self.layout.helper.read_bytes(), b"old-helper")
        self.assertFalse(self.services.manager_active)
        self.assertEqual(self.services.calls, [["systemctl", "stop", recovery.SERVICE]])

    def test_healthy_active_guest_is_not_destroyed_or_rewritten(self):
        active = [{"lease": "healthy-lease", "state": "running", "profile": "heavy"}]
        capture = mock.Mock(return_value={"first_job_id": recovery.ACTIVE_JOB})
        with self._patched_common():
            backup, released = recovery.repair(
                self.stage, layout=self.layout, command=self.services,
                probe=lambda *_: active, capture=capture,
            )
        self.assertEqual(backup, self.root / "backup")
        self.assertFalse(released)
        capture.assert_called_once()
        self.assertEqual(self.payloads, [b"new-helper"])
        self.assertEqual(self.services.calls,
                         [["systemctl", "stop", recovery.SERVICE],
                          ["systemctl", "start", recovery.SERVICE]])
        self.assertTrue(self.services.manager_active)

    def test_partial_restart_failure_is_stopped_and_verified(self):
        original = self.services.__call__

        def partial_start(argv, **kwargs):
            if argv[:2] == ["systemctl", "start"]:
                self.services.calls.append(argv)
                self.services.manager_active = True
                raise RuntimeError("synthetic partial activation")
            return original(argv, **kwargs)

        with self._patched_common(), self.assertRaisesRegex(recovery.RecoveryError,
                                                             "restart failed; verified stopped"):
            recovery.repair(self.stage, layout=self.layout, command=partial_start,
                            probe=lambda *_: [], capture=lambda *_: {})
        self.assertFalse(self.services.manager_active)
        self.assertEqual(self.services.calls,
                         [["systemctl", "stop", recovery.SERVICE],
                          ["systemctl", "start", recovery.SERVICE],
                          ["systemctl", "stop", recovery.SERVICE]])

    def test_failed_restop_reports_indeterminate_manager_state(self):
        original = self.services.__call__
        starts = 0

        def failed_restop(argv, **kwargs):
            nonlocal starts
            if argv[:2] == ["systemctl", "start"]:
                starts += 1
                self.services.calls.append(argv)
                self.services.manager_active = True
                raise RuntimeError("synthetic partial activation")
            if starts and argv[:2] == ["systemctl", "stop"]:
                raise RuntimeError("synthetic failed restop")
            return original(argv, **kwargs)

        with self._patched_common(), self.assertRaisesRegex(recovery.RecoveryError,
                                                             "indeterminate manager state"):
            recovery.repair(self.stage, layout=self.layout, command=failed_restop,
                            probe=lambda *_: [], capture=lambda *_: {})
        self.assertTrue(self.services.manager_active)

    def test_unknown_runner_registration_retains_all_gate_claims(self):
        history = {f"{recovery.GATE}:{job}": {"blocked_until": recovery.QUARANTINE_UNTIL,
                                                 "attempts": 1} for job in recovery.GATE_JOBS}
        states = [{"repo": recovery.GATE, "lease": "active-lease", "runner_id": 7,
                   "trigger_job_id": recovery.ACTIVE_JOB}]
        self.layout.overlay.mkdir()
        (self.layout.overlay / "active-lease").mkdir()
        client = mock.Mock()
        with mock.patch.object(recovery, "raw_state", return_value=(states, history)), \
             mock.patch.object(recovery, "directory"), \
             mock.patch.object(recovery, "gate_roster", return_value=((7, "active", "online", True),
                                                                       (8, "unknown", "offline", False))):
            self.assertFalse(recovery.gate_claims_safe(self.layout, object(),
                                                       [{"lease": "active-lease"}], client))
        client.request.assert_not_called()

    def test_corrupt_raw_lease_fails_closed_before_claim_proof(self):
        self.layout.state.mkdir(mode=0o700)
        bad = self.layout.state / "lease-corrupt.json"
        bad.write_text('{"trigger_job_id": 113175561905,')
        bad.chmod(0o600)
        identity = types.SimpleNamespace(pw_uid=os.getuid())
        with mock.patch.object(recovery.pwd, "getpwnam", return_value=identity), \
             self.assertRaises(recovery.StateCorrupt):
            recovery.raw_state(self.layout, object())

    def test_first_job_evidence_uses_kernel_loop_check_and_only_shutdown_reason(self):
        lease = "fixed-lease"
        self.layout.overlay.mkdir(mode=0o710)
        self.layout.qemu_logs.mkdir()
        lease_dir = self.layout.overlay / lease
        lease_dir.mkdir()
        backing = lease_dir / "bounded.img"
        backing.write_bytes(b"synthetic")
        work = lease_dir / "work"
        work.mkdir()
        backup = self.root / "backup"
        backup.mkdir()
        helper = types.SimpleNamespace(
            OVERLAY_ROOT=self.layout.overlay, HELPER_LOCK=self.layout.helper_lock,
            bounded_paths=lambda *_: (lease_dir, backing, work),
            associated_loop=mock.Mock(return_value="/dev/loop7"),
            verify_loop_binding=mock.Mock(),
            host_mount_source=lambda *_: "/dev/loop7",
            local_mount_source=lambda *_: "/dev/loop7",
            name=lambda *_: "sanctuary-ci-fixed-lease",
        )
        safe_log = b"unrelated sensitive fixture line\nshutting down, reason=shutdown\n"
        with mock.patch.object(recovery, "raw_state", return_value=(
                [{"repo": recovery.GATE, "lease": lease, "trigger_job_id": recovery.ACTIVE_JOB}], {})), \
             mock.patch.object(recovery, "load_staged_helper", return_value=helper), \
             mock.patch.object(recovery, "directory"), \
             mock.patch.object(recovery, "regular", return_value=(safe_log, None)), \
             mock.patch.object(recovery, "atomic_write", side_effect=lambda path, data, *_: path.write_bytes(data)):
            result = recovery.first_job_evidence(self.stage, self.layout, object(), backup)
        helper.verify_loop_binding.assert_called_once_with(backing, "/dev/loop7", require_discard=False)
        self.assertEqual(result["shutdown_reason"], "shutdown")
        saved = (backup / "first-job-metadata.json").read_bytes()
        self.assertNotIn(b"sensitive fixture", saved)
        self.assertIn(b"work_free_bytes", saved)
        helper.associated_loop.return_value = None
        (backup / "first-job-metadata.json").unlink()
        with mock.patch.object(recovery, "raw_state", return_value=(
                [{"repo": recovery.GATE, "lease": lease, "trigger_job_id": recovery.ACTIVE_JOB}], {})), \
             mock.patch.object(recovery, "load_staged_helper", return_value=helper), \
             mock.patch.object(recovery, "directory"):
            with self.assertRaisesRegex(recovery.RecoveryError, "loop identity"):
                recovery.first_job_evidence(self.stage, self.layout, object(), backup)
        self.assertFalse((backup / "first-job-metadata.json").exists())

    def _patched_common(self):
        from contextlib import ExitStack
        stack = ExitStack()
        for patcher in self.common_patches():
            stack.enter_context(patcher)
        return stack


if __name__ == "__main__":
    unittest.main()
