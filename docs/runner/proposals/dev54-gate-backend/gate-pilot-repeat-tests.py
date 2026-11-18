import hashlib
from contextlib import redirect_stdout
import io
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

DIRECTORY = Path(__file__).parent
SPEC = importlib.util.spec_from_file_location('dev54_repeat', DIRECTORY / 'gate-pilot-repeat.py')
repeat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repeat)


class RepeatTests(unittest.TestCase):
    def test_previous_trial_must_be_verified_rolled_back_before_new_activation(self):
        for phase, allowed in (('rolled_back', True), ('active_watching', False), ('prepared', False)):
            with self.subTest(phase=phase):
                base = SimpleNamespace(secure_regular=lambda path: json.dumps({'phase': phase}),
                                       main=mock.Mock(return_value=0), Blocked=RuntimeError)
                output = io.StringIO()
                with mock.patch.object(repeat, 'load_base', return_value=base), \
                     mock.patch.object(repeat.sys, 'argv', ['repeat.py']), redirect_stdout(output):
                    self.assertEqual(repeat.main(), 0 if allowed else 1)
                self.assertEqual(base.main.called, allowed)

    def test_new_trial_binds_only_new_identity_and_keeps_exact_corrected_base(self):
        base = repeat.load_base(DIRECTORY)
        self.assertEqual(hashlib.sha256((DIRECTORY / 'gate-pilot-transaction.py').read_bytes()).hexdigest(),
                         repeat.BASE_SHA256)
        self.assertEqual(base.HEAD_SHA, repeat.TRIAL_HEAD)
        self.assertEqual(base.PREACTIVATION_HEAD_SHA, repeat.PREACTIVATION_HEAD)
        self.assertEqual(base.JOURNAL, repeat.JOURNAL)
        self.assertNotEqual(base.JOURNAL, repeat.PREVIOUS_JOURNAL)
        self.assertEqual(str(base.LOCK), '/run/lock/dev54-gate-pilot-transaction.lock')
        self.assertEqual(base.CANDIDATE_HASH, '2faaf1bed35a253fd134bb52bcd2f9ce3e93f204a0bcdc4d96a5a2df925971ce')

    def test_existing_credential_checks_keep_policy_but_use_current_prehead(self):
        base = repeat.load_base(DIRECTORY)
        seen = []
        class Blocked(Exception): pass
        checker = SimpleNamespace(
            Blocked=Blocked,
            credential_checks=lambda cfg, manifest, module: seen.append(manifest),
            gate_candidate_image=lambda path: (base.CANDIDATE_SOURCE, {'sha256': base.CANDIDATE_HASH}),
            candidate_image_php_check=lambda image: seen.append(str(image)),
        )
        base.verify_operator_readiness(DIRECTORY, checker, {}, object())
        original = json.loads((DIRECTORY / 'manifest.json').read_text())
        self.assertEqual(seen[0], {**original, 'gate_remote_head': repeat.PREACTIVATION_HEAD})
        self.assertEqual(seen[1], str(base.CANDIDATE_SOURCE))

    def fixture(self, root):
        base = repeat.load_base(DIRECTORY)
        backup = root / 'backup'; backup.mkdir()
        manager = root / 'manager'; manager.write_bytes(b'original manager')
        cfg = root / 'config'; cfg.write_bytes(b'private synthetic config never output')
        before = {'selector_target': str(base.SOURCE_IMAGE)}
        for path, prefix in ((manager, 'manager'), (cfg, 'config')):
            info = path.stat()
            before.update({prefix + '_uid': info.st_uid, prefix + '_gid': info.st_gid,
                           prefix + '_mode': base.stat.S_IMODE(info.st_mode),
                           prefix + '_sha256': base.sha256_file(path)})
        (backup / 'before.json').write_text(json.dumps(before))
        for path, name in ((manager, 'manager.before'), (cfg, 'manager.toml.before')):
            copy = backup / name; copy.write_bytes(path.read_bytes()); copy.chmod(0o600)
        state = {'phase': 'prepared', 'activated_at': 0, 'before': before, 'backup': str(backup)}
        (backup / 'transaction.json').write_text(json.dumps(state))
        base.JOURNAL = root / 'journal.json'; base.JOURNAL.write_text(json.dumps(state))
        base.MANAGER = manager; base.CONFIG = cfg; base.STATUS = root / 'status.json'
        def read(path, digest=None):
            data = path.read_bytes()
            if digest and hashlib.sha256(data).hexdigest() != digest:
                raise base.Blocked('protected_file_hash_differs')
            return data
        base.secure_regular = read
        return base, state, backup

    def test_full_restore_inputs_are_read_back_before_mutation_and_receipt_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, state, backup = self.fixture(root)
            with mock.patch.object(repeat, 'PACKAGE', root), mock.patch.object(base.os, 'fchown'):
                base.write_json(base.JOURNAL, state)
                receipt = json.loads((root / 'recovery-metadata-verification.json').read_text())
                self.assertTrue(receipt['baseline_metadata_complete'])
                self.assertTrue(receipt['backup_files_readable_and_verified'])
                self.assertTrue(receipt['journal_readback_verified'])
                # The later promotion journal update must not be compared with
                # the immutable initial transaction snapshot a second time.
                base.write_json(base.JOURNAL, {**state, 'activated_at': 1})
                self.assertEqual(json.loads(base.JOURNAL.read_text())['activated_at'], 1)
                self.assertEqual(json.loads((backup / 'transaction.json').read_text()), state)
            self.assertNotIn('private synthetic', json.dumps(receipt))

    def test_incomplete_unreadable_or_corrupt_recovery_input_blocks_continuation(self):
        for kind in ('metadata', 'manager', 'permissions', 'journal'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                base, state, backup = self.fixture(Path(directory))
                if kind == 'metadata':
                    del state['before']['manager_uid']
                    (backup / 'before.json').write_text(json.dumps(state['before']))
                elif kind == 'manager':
                    (backup / 'manager.before').write_bytes(b'corrupt')
                elif kind == 'permissions':
                    (backup / 'manager.toml.before').chmod(0o644)
                else:
                    base.secure_regular = mock.Mock(side_effect=base.Blocked('protected_file_unavailable'))
                with self.assertRaises(base.Blocked):
                    repeat.verify_saved_recovery(base, state)


if __name__ == '__main__':
    unittest.main()
