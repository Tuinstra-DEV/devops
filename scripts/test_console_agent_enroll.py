#!/usr/bin/env python3
"""Security boundary tests: never print CLI tokens or accept SSH option injection."""
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('enroll', Path(__file__).with_name('console-agent-enroll.py'))
enroll = importlib.util.module_from_spec(spec)
spec.loader.exec_module(enroll)


class EnrollmentTests(unittest.TestCase):
    def test_single_token_is_forwarded_with_stdin_newline(self):
        self.assertEqual(enroll.token_from_output(b'Token: cag_synthetic123\n'), b'cag_synthetic123\n')

    def test_missing_or_ambiguous_issuance_output_fails_without_echoing(self):
        for value in (b'none', b'cag_syntheticA cag_syntheticB'):
            with self.assertRaises(RuntimeError) as error:
                enroll.token_from_output(value)
            self.assertNotIn('cag_', str(error.exception))

    def test_ssh_target_cannot_inject_options_or_shell(self):
        for value in ('-oProxyCommand=x', 'root@host;id', 'root@host\n', 'host'):
            with self.assertRaises(ValueError):
                enroll.ssh_command(value, 'true')

    def test_known_ssh_identity_uses_strict_verification(self):
        command = enroll.ssh_command('mtuinstra@vps01.tuinstra.dev', 'true')
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertEqual(command[-2:], ['mtuinstra@vps01.tuinstra.dev', 'true'])

    def test_remote_failure_never_contains_cli_output(self):
        result = subprocess.CompletedProcess([], 1, b'Token: cag_synthetic', b'cag_synthetic')
        with patch.object(enroll.subprocess, 'run', return_value=result):
            with self.assertRaises(RuntimeError) as error:
                enroll.remote('admin@host', ['issue'], data=b'cag_synthetic\n')
        self.assertNotIn('cag_', str(error.exception))

    def test_timeout_reports_uncertain_state_without_command_or_token(self):
        with patch.object(enroll.subprocess, 'run', side_effect=subprocess.TimeoutExpired('secret', 180)):
            with self.assertRaises(RuntimeError) as error:
                enroll.remote('admin@host', ['issue'], data=b'cag_synthetic\n')
        self.assertIn('inspect target/hub state', str(error.exception))
        self.assertNotIn('secret', str(error.exception))


if __name__ == '__main__':
    unittest.main()
