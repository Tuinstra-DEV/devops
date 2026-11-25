"""Release admission ordering and recovery after each interrupted write."""
import importlib.util
import json
from pathlib import Path
import sys
import hashlib
import stat
import tempfile
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

    def test_backup_hash_mismatch_blocks_before_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager_file = root / 'manager'
            config_file = root / 'config'
            selector = root / 'selector'
            manager_file.write_bytes(b'manager')
            config_file.write_bytes(b'changed-after-original-read')
            selector.symlink_to(manager_file)
            backup_root = root / 'backup'
            base = mock.Mock()
            base.MANAGER, base.CONFIG, base.SELECTOR = manager_file, config_file, selector
            base.source_fingerprints.return_value = {
                'manager_sha256': hashlib.sha256(b'manager').hexdigest(),
                'config_sha256': hashlib.sha256(config_file.read_bytes()).hexdigest(),
            }
            def protected(path, expected=None):
                data = path.read_bytes()
                if expected is not None and hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError('protected_file_hash_differs')
                return data
            base.secure_regular.side_effect = protected
            base.repository_map.return_value = {'gate': 1}
            original_lstat = Path.lstat
            def fake_lstat(path):
                info = original_lstat(path)
                if path == selector:
                    return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_gid=info.st_gid)
                if path == backup_root:
                    return SimpleNamespace(st_mode=info.st_mode, st_uid=0)
                return info
            group = {'selected_workflows': ['old']}
            with mock.patch.object(tx, 'BACKUP_ROOT', backup_root),                  mock.patch.object(tx, 'JOURNAL', backup_root / 'active.json'),                  mock.patch.object(tx.preflight, 'verify_policy', return_value=(b'new', {'selected_workflows': ['new']})),                  mock.patch.object(Path, 'lstat', fake_lstat):
                with self.assertRaisesRegex(ValueError, 'protected_file_hash_differs'):
                    tx.prepare(base, mock.Mock(), b'original', group, {})
                self.assertFalse((backup_root / 'active.json').exists())
                base.write_json.assert_not_called()

    def test_rollback_refuses_backup_outside_fixed_directory(self):
        base = mock.Mock()
        base.secure_regular.return_value = json.dumps({'backup': '/unrelated'}).encode()
        with self.assertRaisesRegex(ValueError, 'recovery_backup_path_differs'):
            tx.recover(base, mock.Mock(), mock.Mock())
        base.load_backup_before.assert_not_called()


class RecoveryIntegrationTests(unittest.TestCase):
    def test_real_snapshots_resume_after_each_mutation(self):
        """Real files and production recovery code, with host and API as fixtures."""
        for fault in ('config', 'start', 'group'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                backup_root, backup = root / 'backups', root / 'backups/snapshot'
                backup.mkdir(parents=True)
                config = root / 'manager.toml'
                selector = root / 'selector'
                selector.symlink_to('image')
                selector_info = selector.lstat()
                manager_path = root / 'manager'
                manager_path.write_bytes(b'manager')
                group_old = {'id': 4, 'selected_workflows': [load_plan.PREFIX + load_plan.OLD] + ['other-' + str(i) for i in range(7)],
                             'visibility': 'selected', 'restricted_to_workflows': True, 'allows_public_repositories': False}
                group_new = load_plan.group_delta(group_old)
                original = ('org_execution_roles = { ' + ', '.join(
                    role + ' = { workflow_ref = "' + load_plan.PREFIX + load_plan.OLD + '", '
                    'repositories = ["Tuinstra-DEV/gate"], events = ["pull_request"], '
                    'actors = [{login="marcel-tuinstra", type="User"}] }' for role in load_plan.ROLES) + ' }\n').encode()
                candidate = load_plan.render_config(original)
                config.write_bytes(candidate)
                repositories = {'Tuinstra-DEV/gate': 1273386924}
                fixed = {'manager_sha256': hashlib.sha256(b'manager').hexdigest(),
                         'config_sha256': hashlib.sha256(original).hexdigest(),
                         'helper_sha256': 'helper', 'selector_target': 'image', 'active_image_sha256': 'imagehash',
                         'manager_uid': 1, 'manager_gid': 1, 'manager_mode': 0o755,
                         'config_uid': 1, 'config_gid': 1, 'config_mode': 0o600,
                         'selector_link': 'image', 'selector_uid': selector_info.st_uid,
                         'selector_gid': selector_info.st_gid, 'selector_mode': stat.S_IMODE(selector_info.st_mode)}
                state = {'phase': 'prepared', 'backup': str(backup), 'before': fixed,
                         'config_after_sha256': hashlib.sha256(candidate).hexdigest()}
                for name, data in [('manager.toml.before', original), ('manager.toml.after', candidate),
                                   ('group-before.json', json.dumps(group_old).encode()),
                                   ('group-after.json', json.dumps(group_new).encode()),
                                   ('repositories-before.json', json.dumps(repositories).encode())]:
                    (backup / name).write_bytes(data)
                journal = backup_root / 'active.json'
                journal.write_text(json.dumps(state))
                base, manager, checker = mock.Mock(), mock.Mock(), mock.Mock()
                base.CONFIG, base.MANAGER, base.SELECTOR, base.NEW_IMAGE, base.SERVICE = config, manager_path, selector, Path('/image'), 'manager'
                live = {'running': False, 'group': group_new, 'fault': fault}
                def interrupt(point):
                    if live['fault'] == point:
                        live['fault'] = None
                        raise InterruptedError(point)
                def secure(path, expected=None):
                    data = path.read_bytes()
                    if expected and hashlib.sha256(data).hexdigest() != expected: raise ValueError('hash_drift')
                    return data
                def service(): return {'ActiveState': 'active' if live['running'] else 'inactive'}
                def command(args):
                    if args[1] == 'start': live['running'] = True; interrupt('start')
                    elif args[1] == 'stop': live['running'] = False
                def write_config(path, data, uid, gid, mode):
                    path.write_bytes(data); interrupt('config')
                def patch_group(client, refs):
                    live['group'] = dict(live['group'], selected_workflows=refs); interrupt('group')
                def get_group(client, cfg):
                    if not live['running']: raise AssertionError('credential requested while stopped')
                    return live['group'], repositories
                def fingerprints(): return {k: fixed[k] for k in ('manager_sha256','helper_sha256','selector_target','active_image_sha256')}
                base.secure_regular.side_effect = secure
                base.load_backup_before.return_value = fixed
                base.source_fingerprints.side_effect = fingerprints
                base.service_state.side_effect = service
                base.safe_command.side_effect = command
                base.atomic_write.side_effect = write_config
                base.patch_group.side_effect = patch_group
                base.parse_manager_config.side_effect = lambda b: load_plan.tomllib.loads(b.decode())
                base.existing_manager_client.side_effect = lambda *a: ('fixture-credential', mock.Mock()) if live['running'] else (_ for _ in ()).throw(AssertionError('credential while stopped'))
                base.read_group.side_effect = get_group
                base.group_policy_matches.return_value = True
                base.repository_map.side_effect = lambda x: x
                base.write_json.side_effect = lambda p, obj: p.write_text(json.dumps(obj))
                manager.configured_org_workflow_refs.return_value = group_old['selected_workflows']
                with mock.patch.object(tx, 'BACKUP_ROOT', backup_root), mock.patch.object(tx, 'JOURNAL', journal):
                    with self.assertRaises(InterruptedError): tx.recover(base, manager, checker)
                    tx.recover(base, manager, checker)
                self.assertEqual(config.read_bytes(), original)
                self.assertEqual(live['group'], group_old)
                self.assertTrue(live['running'])
                self.assertEqual(json.loads(journal.read_text())['phase'], 'rolled_back')
                base.verify_restored_files.assert_called_once_with(fixed)
