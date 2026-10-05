#!/usr/bin/env python3
"""DEV-33 test harness command boundary regressions; no Docker or network."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_harness', Path(__file__).with_name('test_isolated_restore_native.py'))
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

class AdapterBoundaryTest(unittest.TestCase):
    def test_actual_wrapper_preserves_bounded_cause_and_exit(self):
        cases = [(1, 'pg_restore: input file does not appear to be a valid archive', 'pg_restore'),
                 (68, 'isolated Umami did not become healthy', 'application-health'),
                 (70, 'isolated restore cleanup failed', 'cleanup')]
        for exit_code, diagnostic, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory(prefix='dev33-boundary-') as directory:
                root = Path(directory)
                adapter = root / 'synthetic-adapter'
                adapter.write_text(f'#!/bin/bash\nprintf "%s\\n" "{diagnostic}" >&2\nexit {exit_code}\n')
                adapter.chmod(0o700)
                event = root / 'event.json'
                wrapper = harness.instrument_adapter(root, 'case', adapter, event)
                result = subprocess.run([str(wrapper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
                self.assertEqual(exit_code, result.returncode)
                self.assertEqual({'exit_code': exit_code, 'failure_class': expected}, json.loads(event.read_text()))
                self.assertFalse((wrapper.parent / 'adapter.stderr').exists())

    def test_success_stdout_is_forwarded_without_false_failure(self):
        with tempfile.TemporaryDirectory(prefix='dev33-boundary-') as directory:
            root = Path(directory)
            adapter = root / 'synthetic-adapter'
            adapter.write_text('#!/bin/bash\nprintf "{\\\"passed\\\":true}\\n"\n')
            adapter.chmod(0o700)
            event = root / 'event.json'
            wrapper = harness.instrument_adapter(root, 'case', adapter, event)
            result = subprocess.run([str(wrapper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(0, result.returncode)
            self.assertEqual({'passed': True}, json.loads(result.stdout))
            self.assertEqual(b'', result.stderr)
            self.assertEqual(0, json.loads(event.read_text())['exit_code'])

if __name__ == '__main__':
    unittest.main()
