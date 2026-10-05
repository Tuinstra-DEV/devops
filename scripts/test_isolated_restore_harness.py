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

class RuntimeOwnershipTest(unittest.TestCase):
    def test_preexisting_directory_is_preserved_on_refused_creation(self):
        with tempfile.TemporaryDirectory(prefix='dev33-ownership-') as directory:
            parent = Path(directory)
            existing = parent / 'dev33-native-existing'
            existing.mkdir()
            sentinel = existing / 'witness'
            sentinel.write_text('pre-existing synthetic state')
            self.assertTrue(harness.cleanup_owned_runtime(existing, False, parent, existing.name))
            self.assertEqual('pre-existing synthetic state', sentinel.read_text())

    def test_only_exact_created_directory_is_removed(self):
        with tempfile.TemporaryDirectory(prefix='dev33-ownership-') as directory:
            parent = Path(directory)
            owned = parent / 'dev33-native-owned'
            owned.mkdir()
            (owned / 'witness').write_text('own fixture')
            self.assertFalse(harness.cleanup_owned_runtime(owned, True, parent, 'other-name'))
            self.assertTrue(owned.exists())
            self.assertTrue(harness.cleanup_owned_runtime(owned, True, parent, owned.name))
            self.assertFalse(owned.exists())

    def test_symlink_cleanup_does_not_touch_its_target(self):
        with tempfile.TemporaryDirectory(prefix='dev33-ownership-') as directory:
            parent = Path(directory)
            target = parent / 'unrelated'
            target.mkdir()
            link = parent / 'dev33-native-owned'
            link.symlink_to(target, target_is_directory=True)
            self.assertFalse(harness.cleanup_owned_runtime(link, True, parent, link.name))
            self.assertTrue(link.is_symlink())
            self.assertTrue(target.is_dir())

if __name__ == '__main__':
    unittest.main()
