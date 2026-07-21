from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ci_runner_host_helper as helper


class HostHelperTests(unittest.TestCase):
    def test_domain_name_is_namespaced(self):
        self.assertEqual(helper.name("job-9"), "sanctuary-ci-job-9")

    def test_lease_directory_cannot_escape_overlay_root(self):
        for value in ("../etc", "a/b", "white space", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                helper.lease_dir(value)

    @mock.patch.object(helper, "run")
    @mock.patch.object(helper, "resolved_base_image")
    @mock.patch.object(helper.os, "geteuid", return_value=0)
    def test_launch_rejects_existing_domain_before_reading_secret(self, _euid, image, run):
        image.return_value = mock.Mock()
        run.return_value = mock.Mock(stdout="sanctuary-ci-existing\n")
        with self.assertRaisesRegex(RuntimeError, "domain already exists"):
            helper.launch("new")

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
                    mock.patch.object(helper.sys, "stdin", stdin):
                helper.launch("lease")

        self.assertIn(mock.call([
            "systemd-run", "--unit", "sanctuary-ci-expire-lease",
            "--on-active", "7200s", "--timer-property", "AccuracySec=30s",
            "/usr/local/libexec/ci-runner-host-helper", "destroy", "lease",
        ]), run.call_args_list)


if __name__ == "__main__":
    unittest.main()
