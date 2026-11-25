"""Interrupt and retry the fixed-purpose release ref recovery sequence."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import unittest

path = Path(__file__).resolve().parents[1] / 'docs/runner/proposals/dev54-release-check-read/recovery.py'
spec = importlib.util.spec_from_file_location('release_recovery', path)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


class Adapter:
    def __init__(self, config, group, running, fail_at=None):
        self.config, self.group, self.running = config, group, running
        self.repositories = '11-repositories'
        self.fail_at = fail_at
        self.fixed = 'manager-image-selector-helper'

    def checkpoint(self, name):
        if self.fail_at == name:
            self.fail_at = None
            raise InterruptedError(name)

    def ensure_empty(self): self.checkpoint('empty')
    def verify_fixed_resources(self, before):
        if self.fixed != before.fixed: raise ValueError('fixed_drift')
    def read_config(self): return self.config
    def service_running(self): return self.running
    def read_group_and_repositories(self):
        if not self.running: raise ValueError('credential_service_stopped')
        return self.group, self.repositories
    def stop_service(self): self.running = False; self.checkpoint('stop')
    def write_config(self, value): self.config = value; self.checkpoint('config')
    def start_service(self): self.running = True; self.checkpoint('start')
    def restore_group(self, value): self.group = value; self.checkpoint('group')
    def verify_restored(self, before):
        self.checkpoint('verify')
        if (self.config, self.group, self.repositories, self.running, self.fixed) != (
                before.config, before.group, before.repositories, True, before.fixed):
            raise ValueError('readback_differs')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.before = SimpleNamespace(config='old', group='old-group', repositories='11-repositories', fixed='manager-image-selector-helper')
        self.after = SimpleNamespace(config='new', group='new-group')

    def test_every_partial_state_and_interruption_is_resumable(self):
        for config in ('old', 'new'):
            for group in ('old-group', 'new-group'):
                for running in (False, True):
                    for fault in ('empty', 'stop', 'config', 'start', 'group', 'verify'):
                        with self.subTest(config=config, group=group, running=running, fault=fault):
                            adapter = Adapter(config, group, running, fault)
                            try: recovery.restore(adapter, self.before, self.after)
                            except InterruptedError: pass
                            adapter.fail_at = None
                            recovery.restore(adapter, self.before, self.after)
                            adapter.verify_restored(self.before)

    def test_unknown_drift_never_overwritten(self):
        for field, value in [('config', 'foreign'), ('group', 'foreign'),
                             ('repositories', 'foreign'), ('fixed', 'foreign')]:
            with self.subTest(field=field):
                adapter = Adapter('new', 'new-group', True)
                setattr(adapter, field, value)
                with self.assertRaises(ValueError): recovery.restore(adapter, self.before, self.after)
                self.assertEqual(getattr(adapter, field), value)


class LiveAdapterTests(unittest.TestCase):
    def test_credential_is_reacquired_after_service_restart(self):
        location = path.with_name('live_recovery.py')
        loader = importlib.util.spec_from_file_location('live_release_recovery', location)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        base, manager, checker = mock.Mock(), mock.Mock(), mock.Mock()
        base.CONFIG = Path('/fixture/manager.toml')
        base.SERVICE = 'ci-runner-manager.service'
        base.secure_regular.return_value = b'original'
        base.parse_manager_config.return_value = {'policy': 'fixture'}
        client = mock.Mock()
        base.existing_manager_client.return_value = ('credential-sentinel', client)
        base.read_group.return_value = ({'selected_workflows': ['old']}, {'repositories': []})
        base.group_policy_matches.return_value = True
        base.repository_map.return_value = {'gate': 1}
        adapter = module.LiveRecovery(base, manager, checker, Path('/fixture'), {})
        self.assertEqual(adapter.read_group_and_repositories(), ({'selected_workflows': ['old']}, {'gate': 1}))
        base.existing_manager_client.assert_called_once_with({'policy': 'fixture'}, checker, manager)
        base.read_group.assert_called_once_with(client, {})

    def test_config_write_requires_byte_exact_protected_backup(self):
        location = path.with_name('live_recovery.py')
        loader = importlib.util.spec_from_file_location('live_release_recovery_backup', location)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        base = mock.Mock()
        base.secure_regular.return_value = b'original'
        meta = {'config_sha256': 'expected', 'config_uid': 0, 'config_gid': 1, 'config_mode': 0o600}
        adapter = module.LiveRecovery(base, mock.Mock(), mock.Mock(), Path('/fixture'), meta)
        with self.assertRaisesRegex(ValueError, 'recovery_backup_differs'):
            adapter.write_config(b'wrong')
        base.atomic_write.assert_not_called()
        adapter.write_config(b'original')
        base.atomic_write.assert_called_once_with(base.CONFIG, b'original', 0, 1, 0o600)

    def test_selector_link_and_metadata_drift_fail_closed(self):
        import os
        import stat
        import tempfile
        location = path.with_name('live_recovery.py')
        loader = importlib.util.spec_from_file_location('live_release_recovery_selector', location)
        module = importlib.util.module_from_spec(loader)
        loader.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            selector = Path(temporary) / 'selector'
            selector.symlink_to('image')
            info = selector.lstat()
            fixed = {'manager_sha256': 'manager', 'helper_sha256': 'helper',
                     'selector_target': '/image', 'active_image_sha256': 'hash',
                     'selector_link': 'image', 'selector_uid': info.st_uid,
                     'selector_gid': info.st_gid, 'selector_mode': stat.S_IMODE(info.st_mode)}
            base = mock.Mock()
            base.SELECTOR = selector
            base.source_fingerprints.return_value = fixed
            adapter = module.LiveRecovery(base, mock.Mock(), mock.Mock(), Path(temporary), {})
            adapter.verify_fixed_resources(SimpleNamespace(fixed=fixed))
            with mock.patch.object(module.os, 'readlink', return_value='different'):
                with self.assertRaisesRegex(ValueError, 'recovery_selector_metadata_drift'):
                    adapter.verify_fixed_resources(SimpleNamespace(fixed=fixed))
            with self.assertRaisesRegex(ValueError, 'recovery_selector_metadata_drift'):
                adapter.verify_fixed_resources(SimpleNamespace(fixed=fixed | {'selector_uid': info.st_uid + 1}))
