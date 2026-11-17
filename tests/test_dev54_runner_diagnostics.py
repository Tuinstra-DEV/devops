import importlib.util
import contextlib
import io
import json
from pathlib import Path
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'docs/runner/proposals/dev54-sanctuary-recovery/diagnose.py'
spec = importlib.util.spec_from_file_location('runner_diagnostics', SOURCE)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)
watch_spec = importlib.util.spec_from_file_location('vm_watch', SOURCE.with_name('watch-vm.py'))
watch = importlib.util.module_from_spec(watch_spec)
watch_spec.loader.exec_module(watch)


class DiagnosticsTests(unittest.TestCase):
    def test_followup_watch_preserves_primary_and_never_overwrites_a_phase(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            def observe(phase=None):
                args = ['watch-vm.py', '--job', '134383118242', '--duration', '1']
                if phase:
                    args += ['--phase', phase]
                with patch('sys.argv', args), patch.object(Path, 'home', return_value=home), \
                        patch.object(watch.time, 'monotonic', side_effect=[0, 0, 2]), \
                        patch.object(watch.time, 'sleep'), patch.object(watch, 'query', return_value=None), \
                        patch.object(watch, 'storage_stats', return_value={'metadata_readable': False}), \
                        contextlib.redirect_stdout(io.StringIO()):
                    watch.main()
            observe()
            directory = home / 'dev54-vm-watch-134383118242'
            primary = (directory / 'samples.jsonl').read_bytes()
            observe('followup')
            self.assertEqual((directory / 'samples.jsonl').read_bytes(), primary)
            followup = (directory / 'followup-samples.jsonl').read_bytes()
            with self.assertRaises(FileExistsError):
                observe('followup')
            self.assertEqual((directory / 'followup-samples.jsonl').read_bytes(), followup)

    def test_storage_fault_classification_does_not_emit_log_contents(self):
        text = 'private-credential-sentinel\nwrite failed: No space left on device\nInput/output error\nterminating on signal 15 from secret-process-details'
        value = diagnostics.classify(text)
        self.assertEqual(value['termination_signals'], [15])
        self.assertIn('qemu_storage_full', value['categories'])
        self.assertIn('qemu_io_error', value['categories'])
        self.assertNotIn('sentinel', json.dumps(value))
        self.assertNotIn('secret-process', json.dumps(value))

    def test_helper_values_are_parsed_without_executing_source(self):
        source = 'raise RuntimeError("must not execute")\nPROFILE_DISK_GIB={"heavy":24,"medium":4}\nPRIVATE="sentinel"\nDISK_GIB="120G"\n'
        self.assertEqual(diagnostics.helper_limits(source), {'PROFILE_DISK_GIB': {'heavy': 24, 'medium': 4}, 'DISK_GIB': '120G'})

    def test_missing_operator_privilege_never_reads_files(self):
        with patch.object(diagnostics.os, 'geteuid', return_value=1000), patch.object(Path, 'read_bytes') as read:
            self.assertEqual(diagnostics.main()['blocker'], 'existing_operator_sudo_required')
            read.assert_not_called()

    def test_vm_paused_io_and_disk_full_are_distinct_from_shutdown(self):
        self.assertEqual(watch.state_value('paused (I/O error)\n'), {'state': 'paused', 'io_error_reason': True})
        self.assertEqual(watch.state_value('shut off (destroyed)')['state'], 'shut off')
        self.assertEqual(watch.block_errors('vda: no space'), ['storage_full'])
        self.assertEqual(watch.block_errors('No errors found'), [])

    def test_vm_stats_never_serialize_paths_or_other_unbounded_values(self):
        value = watch.metrics('Domain: private\nblock.0.path=/private/sentinel\nblock.0.allocation=24000\nstate.state=3\n')
        self.assertEqual(value, {'block.0.allocation': 24000, 'state.state': 3})
        self.assertNotIn('sentinel', json.dumps(value))

    def test_bounded_storage_reads_metadata_only_and_rejects_other_mount(self):
        stats = SimpleNamespace(f_blocks=6100000, f_frsize=4096, f_bavail=0)
        with patch.object(watch.os, 'statvfs', return_value=stats):
            result = watch.storage_stats('134333682935')
        self.assertTrue(result['bounded_volume'])
        self.assertEqual(result['free_bytes'], 0)
        with patch.object(watch.os, 'statvfs', side_effect=PermissionError('private sentinel')):
            self.assertFalse(watch.storage_stats('134333682935')['metadata_readable'])
        with self.assertRaises(ValueError):
            watch.storage_stats('../private')


if __name__ == '__main__':
    unittest.main()
