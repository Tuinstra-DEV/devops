import subprocess
import unittest
from unittest import mock

import ci_wow_cpu_pin as pin


IDENTITY = "a" * 64


class WowCpuPinTests(unittest.TestCase):
    def test_equivalent_cpu_ranges_are_accepted_without_update(self):
        with mock.patch.object(pin.subprocess, "run", return_value=mock.Mock(
            stdout=f"{IDENTITY}|true|1-3,9-11\n")) as run:
            pin.reconcile()
        self.assertEqual(run.call_count, 1)

    def test_unrestricted_container_is_pinned_and_verified(self):
        outputs = [mock.Mock(stdout=f"{IDENTITY}|true|\n"),
                   mock.Mock(stdout=""),
                   mock.Mock(stdout=f"{IDENTITY}|true|1,9,2,10,3,11\n")]
        with mock.patch.object(pin.subprocess, "run", side_effect=outputs) as run:
            pin.reconcile()
        self.assertEqual(run.call_args_list[1].args[0],
                         [pin.DOCKER, "update", "--cpuset-cpus=1,9,2,10,3,11", IDENTITY])

    def test_stopped_container_fails_closed(self):
        with mock.patch.object(pin.subprocess, "run", return_value=mock.Mock(
            stdout=f"{IDENTITY}|false|\n")) as run:
            with self.assertRaisesRegex(RuntimeError, "not running"):
                pin.reconcile()
        self.assertEqual(run.call_count, 1)

    def test_failed_update_does_not_report_success(self):
        with mock.patch.object(pin.subprocess, "run", side_effect=[
            mock.Mock(stdout=f"{IDENTITY}|true|\n"),
            subprocess.CalledProcessError(1, [pin.DOCKER, "update"]),
        ]):
            with self.assertRaises(subprocess.CalledProcessError):
                pin.reconcile()

    def test_post_update_wrong_cpu_set_fails_closed(self):
        outputs = [mock.Mock(stdout=f"{IDENTITY}|true|\n"),
                   mock.Mock(stdout=""),
                   mock.Mock(stdout=f"{IDENTITY}|true|0-15\n")]
        with mock.patch.object(pin.subprocess, "run", side_effect=outputs):
            with self.assertRaisesRegex(RuntimeError, "did not verify"):
                pin.reconcile()

    def test_invalid_cpu_set_is_rejected(self):
        with self.assertRaises(ValueError):
            pin.parse_cpu_set("3-1")


if __name__ == "__main__":
    unittest.main()
