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
    @mock.patch.object(helper, "BASE_IMAGE")
    @mock.patch.object(helper.os, "geteuid", return_value=0)
    def test_launch_rejects_existing_domain_before_reading_secret(self, _euid, image, run):
        image.is_file.return_value = True
        run.return_value = mock.Mock(stdout="sanctuary-ci-existing\n")
        with self.assertRaisesRegex(RuntimeError, "domain already exists"):
            helper.launch("new")


if __name__ == "__main__":
    unittest.main()
