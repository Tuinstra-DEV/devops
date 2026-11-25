"""Release admission ordering and recovery after each interrupted write."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] / 'docs/runner/proposals/dev54-release-check-read'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    result = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {'plan': load_plan, 'preflight': load_preflight,
                                      'recovery': load_recovery, 'live_recovery': load_live}):
        spec.loader.exec_module(result)
    return result


def simple(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


load_plan = simple('test_plan', 'plan.py')
with mock.patch.dict(sys.modules, {'plan': load_plan}):
    load_preflight = simple('test_preflight', 'preflight.py')
load_recovery = simple('test_recovery', 'recovery.py')
load_live = simple('test_live_recovery', 'live_recovery.py')
tx = load('test_transaction', 'transaction.py')


class ActivationTests(unittest.TestCase):
    def test_journal_precedes_mutation_and_failure_invokes_recovery(self):
        for failure in ('stop', 'group', 'config', 'start', 'policy'):
            with self.subTest(failure=failure):
                calls = []
                base = mock.Mock()
                manager, checker, client = mock.Mock(), mock.Mock(), mock.Mock()
                original, candidate = b'old', b'new'
                group = {'selected_workflows': ['old']}
                target = {'selected_workflows': ['new']}
                repos = {'gate': 1}
                state = {'before': {'config_uid': 0, 'config_gid': 0, 'config_mode': 0o600}}
                base.MAX_WAIT_SECONDS = 1
                base.SERVICE = 'manager'
                base.CONFIG = Path('/config')
                base.secure_regular.return_value = candidate
                base.parse_manager_config.return_value = {}
                base.existing_manager_client.return_value = ('sentinel', client)
                base.read_group.return_value = (target, repos)
                base.repository_map.side_effect = lambda x: x
                manager.configured_org_workflow_refs.return_value = ['new']
                def mark(name):
                    def inner(*args, **kwargs):
                        calls.append(name)
                        if name == failure: raise RuntimeError(name)
                    return inner
                base.stop_manager_when_empty.side_effect = mark('stop')
                base.patch_group.side_effect = mark('group')
                base.atomic_write.side_effect = mark('config')
                base.safe_command.side_effect = mark('start')
                manager.validate_org_config.side_effect = mark('policy')
                with mock.patch.object(tx, 'verify_baseline'), \
                     mock.patch.object(tx, 'prepare', side_effect=lambda *a: (calls.append('journal') or (state, Path('/backup'), candidate, target))), \
                     mock.patch.object(tx, 'recover', side_effect=mark('recover')):
                    with self.assertRaises(RuntimeError):
                        tx.activate(base, manager, checker, client, original, group, repos)
                self.assertLess(calls.index('journal'), calls.index(failure))
                self.assertEqual(calls[-1], 'recover')

    def test_rollback_refuses_backup_outside_fixed_directory(self):
        base = mock.Mock()
        base.secure_regular.return_value = json.dumps({'backup': '/unrelated'}).encode()
        with self.assertRaisesRegex(ValueError, 'recovery_backup_path_differs'):
            tx.recover(base, mock.Mock(), mock.Mock())
        base.load_backup_before.assert_not_called()
