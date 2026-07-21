import base64
import contextlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import ci_runner_host_helper as helper


class HostHelperTests(unittest.TestCase):
    request_id = "a" * 32

    @staticmethod
    def decoded_response(connection):
        return json.loads(connection.sendall.call_args.args[0])

    @staticmethod
    def connection_for_uid(uid):
        connection = mock.Mock()
        connection.getsockopt.return_value = struct.pack("3i", 123, uid, 456)
        return connection

    def test_domain_name_is_namespaced(self):
        self.assertEqual(helper.name("job-9"), "sanctuary-ci-job-9")

    def test_cloud_init_queues_runner_after_cloud_final_without_deadlock(self):
        user_data = helper.cloud_init_user_data(base64.b64encode(b"opaque-jit"))
        self.assertIn("- [systemctl, start, --no-block, ci-runner-job.service]", user_data)
        self.assertNotIn("- [systemctl, start, ci-runner-job.service]", user_data)

    def test_lease_directory_cannot_escape_overlay_root(self):
        for value in ("../etc", "a/b", "white space", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                helper.lease_dir(value)

    @mock.patch.object(helper, "run")
    @mock.patch.object(helper, "resolved_base_image")
    @mock.patch.object(helper.os, "geteuid", return_value=0)
    def test_launch_rejects_existing_domain_before_mutation(self, _euid, image, run):
        image.return_value = mock.Mock()
        run.return_value = mock.Mock(stdout="sanctuary-ci-existing\n")
        with self.assertRaisesRegex(RuntimeError, "domain already exists"):
            helper.launch("new", b"aml0")

    @mock.patch.object(helper.Path, "is_file", return_value=True)
    @mock.patch.object(helper.Path, "resolve")
    def test_base_image_must_resolve_to_digest_versioned_file(self, resolve, _is_file):
        resolve.return_value = helper.IMAGE_ROOT / ("ubuntu-24.04-runner-" + "a" * 64 + ".qcow2")
        self.assertEqual(helper.resolved_base_image(), resolve.return_value)

        resolve.return_value = helper.IMAGE_ROOT / "ubuntu-24.04-runner.qcow2"
        with self.assertRaisesRegex(ValueError, "digest-versioned"):
            helper.resolved_base_image()

    @mock.patch.object(helper.os, "geteuid", return_value=0)
    @mock.patch.object(helper, "resolved_base_image")
    @mock.patch.object(helper, "run")
    def test_launch_schedules_independent_host_expiry_timer(self, run, image, _euid):
        with tempfile.TemporaryDirectory() as directory:
            stdin = mock.Mock()
            stdin.buffer.read.return_value = b"aml0"
            image.return_value = helper.IMAGE_ROOT / ("ubuntu-24.04-runner-" + "a" * 64 + ".qcow2")
            run.return_value = mock.Mock(stdout="")
            with mock.patch.object(helper, "lease_dir", return_value=Path(directory) / "lease"), \
                    mock.patch.object(helper.sys, "stdin", stdin), \
                    mock.patch.object(helper, "qemu_identity", return_value=(64055, 994)), \
                    mock.patch.object(helper, "set_qemu_access") as set_access:
                helper.launch("lease")

            lease = Path(directory) / "lease"
            self.assertEqual(set_access.call_args_list, [
                mock.call(lease, 0, 994, 0o710),
                mock.call(lease / "root.qcow2", 64055, 994, 0o600),
                mock.call(lease / "seed.iso", 64055, 994, 0o600),
            ])

        self.assertIn(mock.call([
            "systemd-run", "--unit", "sanctuary-ci-expire-lease",
            "--on-active", "7200s", "--timer-property", "AccuracySec=30s",
            "--property=NoNewPrivileges=yes", "--property=ProtectSystem=strict",
            "--property=ProtectHome=yes", "--property=PrivateTmp=yes",
            "--property=ReadWritePaths=/mnt/ssd1000-01/ci-runner /run/lock",
            "--property=RestrictAddressFamilies=AF_UNIX",
            "/usr/local/libexec/ci-runner-host-helper", "destroy", "lease",
        ]), run.call_args_list)

    @mock.patch.object(helper.os, "chmod")
    @mock.patch.object(helper.os, "chown")
    def test_qemu_access_sets_exact_owner_group_and_mode(self, chown, chmod):
        path = Path("/runner/seed.iso")
        helper.set_qemu_access(path, 64055, 994, 0o600)
        chown.assert_called_once_with(path, 64055, 994)
        chmod.assert_called_once_with(path, 0o600)

    def test_serve_rejects_untrusted_peer_before_reading_request(self):
        connection = self.connection_for_uid(1001)

        helper.serve_connection(connection, expected_uid=1002)

        connection.recv.assert_not_called()
        self.assertEqual(self.decoded_response(connection)["error"], "unauthorized")

    def test_parse_request_enforces_exact_operation_schemas(self):
        valid = [
            {"v": 1, "id": self.request_id, "op": "list"},
            {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1"},
            {"v": 1, "id": self.request_id, "op": "destroy", "lease": "job-1"},
        ]
        for request in valid:
            with self.subTest(request=request):
                self.assertEqual(helper.parse_request(json.dumps(request).encode()), request)

        invalid = [
            {"v": True, "id": self.request_id, "op": "list"},
            {"v": 1, "id": self.request_id.upper(), "op": "list"},
            {"v": 1, "id": self.request_id, "op": "unknown"},
            {"v": 1, "id": self.request_id, "op": "list", "lease": "extra"},
            {"v": 1, "id": self.request_id, "op": "destroy"},
            {"v": 1, "id": self.request_id, "op": "launch", "lease": "../escape"},
            {"v": 1, "id": self.request_id, "op": "list", "extra": False},
        ]
        for request in invalid:
            with self.subTest(request=request), self.assertRaises(helper.ProtocolError):
                helper.parse_request(json.dumps(request).encode())
        duplicate = ('{"v":1,"id":"' + self.request_id +
                     '","op":"list","op":"list"}').encode()
        with self.assertRaises(helper.ProtocolError):
            helper.parse_request(duplicate)

    def test_request_and_jit_packets_are_size_limited(self):
        with self.assertRaises(helper.ProtocolError):
            helper.parse_request(b"x" * (helper.MAX_REQUEST_BYTES + 1))
        with self.assertRaises(helper.ProtocolError):
            helper.validate_jit_payload(b"Y" * (helper.MAX_JIT_BYTES + 1))

    @mock.patch.object(helper, "launch")
    def test_serve_passes_validated_raw_jit_packet_to_launch(self, launch):
        connection = self.connection_for_uid(1002)
        request = {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1"}
        jit = base64.b64encode(b"ephemeral registration material")
        connection.recv.side_effect = [json.dumps(request).encode(), jit]
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(helper, "HELPER_LOCK", Path(directory) / "helper.lock"):
            helper.serve_connection(connection, expected_uid=1002)

        launch.assert_called_once_with("job-1", jit)
        self.assertEqual(connection.settimeout.call_args_list, [mock.call(5.0), mock.call(None)])
        self.assertEqual(self.decoded_response(connection), {
            "v": 1, "id": self.request_id, "ok": True, "result": None,
        })

    @mock.patch.object(helper, "launch")
    def test_serve_validates_jit_before_launch_mutation(self, launch):
        connection = self.connection_for_uid(1002)
        request = {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1"}
        connection.recv.side_effect = [json.dumps(request).encode(), b"not base64!!"]

        helper.serve_connection(connection, expected_uid=1002)

        launch.assert_not_called()
        self.assertEqual(self.decoded_response(connection)["error"], "invalid_request")
        self.assertEqual(self.decoded_response(connection)["id"], self.request_id)

    @mock.patch.object(helper, "launch")
    def test_operation_errors_are_sanitized_in_response(self, launch):
        connection = self.connection_for_uid(1002)
        request = {"v": 1, "id": self.request_id, "op": "launch", "lease": "job-1"}
        secret = "sensitive-jit-material"
        connection.recv.side_effect = [json.dumps(request).encode(), base64.b64encode(b"jit")]
        launch.side_effect = RuntimeError(secret)
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(helper, "HELPER_LOCK", Path(directory) / "helper.lock"), \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            helper.serve_connection(connection, expected_uid=1002)

        encoded = connection.sendall.call_args.args[0]
        self.assertNotIn(secret.encode(), encoded)
        self.assertNotIn(secret, stderr.getvalue())
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertEqual(json.loads(encoded)["error"], "operation_failed")

    @mock.patch.object(helper.subprocess, "run")
    def test_command_failure_keeps_output_secret_and_exposes_safe_stage(self, run):
        secret = "sensitive-command-output"
        run.side_effect = helper.subprocess.CalledProcessError(
            7, ["virt-install"], output="ignored", stderr=secret
        )

        with self.assertRaises(helper.HostCommandError) as raised:
            helper.run(["virt-install", "--connect", "qemu:///system"])

        self.assertEqual(raised.exception.command_name, "virt-install")
        self.assertEqual(raised.exception.returncode, 7)
        self.assertNotIn(secret, str(raised.exception))

    def test_command_diagnostic_rejects_unknown_name_and_normalizes_timeout(self):
        error = helper.HostCommandError("untrusted-command-name", "anything")
        self.assertEqual(error.command_name, "unknown")
        self.assertEqual(error.returncode, "timeout")
        self.assertEqual(str(error), "unknown rc=timeout")

    @mock.patch.object(helper, "run")
    def test_libvirt_commands_use_explicit_system_uri(self, run):
        run.return_value = mock.Mock(stdout="")
        helper.list_leases()
        helper.destroy("lease")

        for call in run.call_args_list:
            command = call.args[0]
            if command[0] == "virsh":
                self.assertEqual(command[1:3], ["--connect", "qemu:///system"])

    def test_response_falls_back_when_result_exceeds_packet_limit(self):
        encoded = helper.encode_response(self.request_id, result="x" * helper.MAX_RESPONSE_BYTES)
        self.assertLessEqual(len(encoded), helper.MAX_RESPONSE_BYTES)
        self.assertEqual(json.loads(encoded)["error"], "operation_failed")


if __name__ == "__main__":
    unittest.main()
