"""No root, network or systemd calls: execute the single script against an isolated fake host."""
import contextlib
import copy
import dataclasses
import grp
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tomllib
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runner/manager'))
import ci_runner_manager as manager


def load_script():
    path = ROOT / 'runner/activation/dev54-activate.sh'
    module = types.ModuleType('dev54_activation_test')
    sys.modules[module.__name__] = module
    code = path.read_text().split("3<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    exec(compile(code, str(path), 'exec'), module.__dict__)
    return module


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.a = load_script()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.a.UID, self.a.ANCHOR = os.getuid(), self.root
        paths = {f.name: self.root / f.name for f in dataclasses.fields(self.a.Layout)}
        paths['drain'] = self.root / 'runtime/manager.d/drain.conf'
        paths['lock'] = self.root / 'private-lock/lock'
        paths['helper_lock'] = self.root / 'locks/helper-lock'
        (self.root / 'locks').mkdir(mode=0o700)
        self.layout = self.a.Layout(**paths)
        for name in ('state', 'overlay'):
            getattr(self.layout, name).mkdir(mode=0o700)
        (self.root / 'runtime').mkdir(mode=0o700)
        self.old = b'old fixture manager'
        self.new = (ROOT / 'runner/manager/ci_runner_manager.py').read_bytes()
        self.a.OLD_MANAGER_SHA = self.a.digest(self.old)
        self.a.HELPER_SHA = self.a.digest(b'fixture helper')
        self.a.UNIT_SHA = self.a.digest(b'fixture unit')
        roles = {role: {'workflow_ref': self.a.LEGACY_METADATA_REF if role == 'metadata-preflight' else
                        self.a.PREFIX + 'reusable-gate-pr-security.yml@' + 'a' * 40,
                        'repositories': ['Tuinstra-DEV/gate', 'Tuinstra-DEV/tracker'],
                        'events': ['pull_request'] if role == 'metadata-preflight' else ['pull_request_target'],
                        'actors': [{'login': 'marcel-tuinstra', 'type': 'User'}]}
                 for role in ('metadata-preflight', 'pr-security-producer')}
        self.cfg = tomllib.loads((ROOT / 'runner/config/manager.toml').read_text())
        self.cfg.update(pool_mode='four', max_concurrency=4, medium_runner_label='trusted-medium',
                        medium_runner_vcpus=2, medium_runner_memory_mib=3072, max_heavy=2, max_medium=2,
                        heavy_disk_reservation_gib=24, medium_disk_reservation_gib=4, storage_mode='bounded-loop',
                        org_routing_enabled=True, org_runner_group_id=4,
                        org_github_token_file=manager.ORG_CREDENTIAL_PATH,
                        org_repository_ids={r: i + 1 for i, r in enumerate(self.a.AFFECTED)},
                        repositories=list(self.a.AFFECTED), org_execution_roles=roles,
                        org_workflow_refs=[manager.ORG_WORKFLOW_PREFIX + c * 40 for c in '123'] +
                                          [self.a.LEGACY_METADATA_REF],
                        state_dir=str(self.layout.state), overlay_root=str(self.layout.overlay))
        for path, data, mode in ((self.layout.manager, self.old, 0o755),
                                  (self.layout.config, self.a.serialize(self.cfg), 0o640),
                                  (self.layout.helper, b'fixture helper', 0o755),
                                  (self.layout.unit, b'fixture unit', 0o644),
                                  (self.layout.org_dropin, self.a.ORG_DROPIN, 0o644)):
            path.write_bytes(data)
            path.chmod(mode)
        self.initial = self.layout.config.read_bytes()
        self.calls, self.groups = [], []
        self.active = True
        self.owner = mock.patch.object(self.a.grp, 'getgrnam', return_value=types.SimpleNamespace(gr_gid=os.getgid()))
        self.owner.start()
        self.addCleanup(self.owner.stop)
        self.overlay_patch = mock.patch.object(manager, 'RUNNER_OVERLAY_ROOT', str(self.layout.overlay))
        self.overlay_patch.start()
        self.addCleanup(self.overlay_patch.stop)
        self.sleep = mock.patch.object(self.a.time, 'sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        owner = self
        class API:
            blocked = False
            group_failure = False
            def __init__(self, *_): pass
            def cve_merged(self):
                owner.calls.append('cve')
                owner.a.require(not self.blocked, 'CVE not merged')
            def quiet(self): owner.calls.append('quiet')
            def group(self, cfg):
                owner.a.require(not self.group_failure, 'group mismatch')
                owner.groups.append(copy.deepcopy(cfg))
        self.API = API
        self.sink = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.sink)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def command(self, args, check=True, timeout=180):
        self.calls.append(tuple(args))
        if args[:2] == ['systemctl', 'stop']:
            self.active = False
        if args[:2] == ['systemctl', 'restart']:
            self.active = True
        if args[:2] == ['systemctl', 'is-active']:
            return 'active' if self.active or args[-1] == self.a.SOCKET else 'inactive'
        if args[:2] == ['systemctl', 'show']:
            property = args[-2].split('=', 1)[1]
            draining = self.layout.drain.exists()
            return {'FragmentPath': str(self.layout.unit), 'Type': 'oneshot' if draining else 'simple',
                    'Restart': 'no' if draining else 'always', 'User': 'ci-runner-manager', 'Group': 'ci-runner-manager',
                    'ExecStart': 'argv[]=/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml ' +
                                 ('reconcile' if draining else 'daemon --interval 30'),
                    'DropInPaths': str(self.layout.org_dropin) + (' ' + str(self.layout.drain) if draining else ''),
                    'ExecMainStatus': '0', 'NRestarts': '0'}[property]
        if args[0] == 'busctl':
            return json.dumps({'type': 'a(ss)', 'data': [['github_token', '/etc/ci-runner/github.token'],
                                                       ['org_github_token', '/etc/ci-runner/org-github.token']]})
        if args[0] in ('virsh', 'systemctl'):
            return ''
        self.fail('unexpected command')

    def apply(self, **kwargs):
        return self.a.apply(self.layout, self.command, loader=lambda: (self.new, manager),
                            api_factory=self.API, confirm=lambda: 'GEREED', pause=lambda _: None, **kwargs)

    def rollback(self):
        return self.a.rollback(self.layout, self.command,
                               api_factory=self.API, confirm=lambda: 'GEREED', pause=lambda _: None)

    def test_pins_match_reviewed_sources(self):
        self.assertEqual(self.a.digest(self.new), self.a.NEW_MANAGER_SHA)
        # CI intentionally has a depth-one checkout. Validate checked-in source
        # bytes without requiring a historical git object or fetching the network.
        self.assertEqual(self.a.SOURCE_COMMIT, '112666ba3e1ee0fe1a5e6c33aed95d8fae0fa823')

    def test_apply_then_rollback_preserves_controls_and_original_bytes(self):
        self.apply()
        actual = tomllib.loads(self.layout.config.read_text())
        self.assertEqual(actual['org_workflow_refs'][:4], self.cfg['org_workflow_refs'])
        self.assertEqual(actual['org_workflow_refs'][4], self.a.TRUSTED_REF)
        self.assertEqual(actual['org_repository_ids'][self.a.TESSERA], 1179209966)
        for role in self.cfg['org_execution_roles']:
            self.assertEqual({k: v for k, v in actual['org_execution_roles'][role].items() if k != 'workflow_ref'},
                             {k: v for k, v in self.cfg['org_execution_roles'][role].items() if k != 'workflow_ref'})
        self.assertEqual(self.layout.manager.read_bytes(), self.new)
        self.assertEqual(self.layout.manager.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.layout.config.stat().st_mode & 0o777, 0o640)
        self.assertFalse(self.layout.drain.exists())
        self.rollback()
        self.assertEqual(self.layout.manager.read_bytes(), self.old)
        self.assertEqual(self.layout.config.read_bytes(), self.initial)
        folder = self.layout.backup / (self.layout.backup / 'last-backup').read_text().strip()
        manifest = json.loads((folder / 'manifest.json').read_text())
        self.assertEqual(manifest['phase'], 'rolled_back')
        self.assertEqual(manifest['new_manager_sha256'], self.a.NEW_MANAGER_SHA)
        self.assertEqual(manifest['role_pin_transitions']['metadata-preflight']['from'], self.a.LEGACY_METADATA_REF)
        self.assertFalse((folder / 'github.token').exists())
        self.assertFalse((folder / 'org-github.token').exists())
        self.assertNotIn('\"selected_repositories\"', self.sink.getvalue())
        self.assertNotIn('\"actors\"', self.sink.getvalue())

    def test_cve_block_happens_before_backup_drain_or_any_mutation(self):
        self.API.blocked = True
        with self.assertRaises(self.a.Refused): self.apply()
        self.assertEqual(self.layout.config.read_bytes(), self.initial)
        self.assertEqual(self.layout.manager.read_bytes(), self.old)
        self.assertFalse(self.layout.backup.exists())
        self.assertFalse(any(isinstance(c, tuple) and c[:2] == ('systemctl', 'stop') for c in self.calls))

    def test_interrupted_manual_group_change_keeps_admission_paused_and_can_rollback(self):
        with mock.patch.object(self.a, 'manual_group', side_effect=EOFError):
            with self.assertRaises(EOFError): self.apply()
        self.assertFalse(self.active)
        self.assertEqual(self.layout.manager.read_bytes(), self.old)
        self.assertEqual(self.layout.config.read_bytes(), self.initial)
        self.assertTrue(self.layout.drain.exists())
        self.rollback()
        self.assertTrue(self.active)

    def test_partial_install_is_rollbackable_and_unrelated_drift_is_refused(self):
        replace = self.a.replace
        def fail_config(path, data, info):
            if path == self.layout.config: raise OSError('synthetic failure')
            return replace(path, data, info)
        with mock.patch.object(self.a, 'replace', side_effect=fail_config):
            with self.assertRaises(OSError): self.apply()
        self.assertEqual(self.layout.manager.read_bytes(), self.new)
        self.assertFalse(self.active)
        self.rollback()
        self.assertEqual(self.layout.manager.read_bytes(), self.old)
        self.apply()
        self.layout.config.write_text('unrelated operator change')
        self.calls.clear()
        with self.assertRaises(self.a.Refused): self.rollback()
        self.assertFalse(any(isinstance(c, tuple) and c[:2] == ('systemctl', 'stop') for c in self.calls))

    def test_config_drift_or_unexpected_binary_aborts_before_stop(self):
        self.layout.manager.write_bytes(b'unknown operator installation')
        with self.assertRaises(self.a.Refused): self.apply()
        self.assertFalse(self.layout.backup.exists())

    def test_candidate_rejects_more_pins_missing_roles_and_prior_repo_admission(self):
        for mutate in (lambda cfg: cfg['org_workflow_refs'].append(self.a.TRUSTED_REF),
                       lambda cfg: cfg['org_execution_roles'].pop('pr-security-producer'),
                       lambda cfg: cfg['org_repository_ids'].update({self.a.TESSERA: self.a.TESSERA_ID})):
            cfg = copy.deepcopy(self.cfg)
            mutate(cfg)
            with self.assertRaises((self.a.Refused, manager.RunnerError)):
                self.a.candidate(cfg, manager)

    def test_private_writes_honor_modes_even_under_restrictive_umask(self):
        old_mask = os.umask(0o077)
        try:
            self.a.write_new(self.root / 'executable', b'test', 0o755)
        finally:
            os.umask(old_mask)
        self.assertEqual((self.root / 'executable').stat().st_mode & 0o777, 0o755)

    def test_symlinks_hardlinks_and_writable_parents_are_rejected(self):
        real = self.root / 'real'
        real.write_bytes(b'test')
        link = self.root / 'link'
        link.symlink_to(real)
        with self.assertRaises(self.a.Refused): self.a.read_file(link)
        os.link(real, self.root / 'hardlink')
        with self.assertRaises(self.a.Refused): self.a.read_file(real)
        unsafe = self.root / 'unsafe'
        unsafe.mkdir(mode=0o700)
        unsafe.chmod(0o777)
        with self.assertRaises(self.a.Refused): self.a.secure_dir(unsafe)

    def test_live_vm_or_near_expiry_prevents_swap_without_destroy(self):
        def live(args, **kwargs):
            if args[0] == 'virsh': return 'sanctuary-ci-existing'
            return self.command(args, **kwargs)
        with self.assertRaises(self.a.Refused): self.a.drain(self.layout, live, seconds=0)
        lease = self.layout.state / 'lease-gh-123.json'
        lease.write_text(json.dumps({'lease': 'gh-123', 'launched_at': int(time.time()) - 7190}))
        with self.assertRaises(self.a.Refused): self.a.drain(self.layout, self.command, seconds=0)
        self.assertFalse(any(isinstance(c, tuple) and any('destroy' in v or 'kill' in v for v in c) for c in self.calls))

    def test_read_only_cve_and_quiet_guards_reject_unmerged_open_or_queued_runs(self):
        api = self.a.GitHub.__new__(self.a.GitHub)
        calls = []
        def get(path):
            calls.append(path)
            if '/actions/runs?' in path: return {'total_count': 0, 'workflow_runs': []}
            if '/pulls?' in path: return []
            return {'number': int(path.rsplit('/', 1)[1]), 'state': 'closed', 'merged': True}
        api.get = get
        api.cve_merged(); api.quiet()
        self.assertEqual(len(calls), 15)
        for value in ({'total_count': 1, 'workflow_runs': [{}]}, {'total_count': None, 'workflow_runs': []}, {'total_count': False, 'workflow_runs': []}):
            api.get = lambda _: value
            with self.assertRaises(self.a.Refused): api.quiet()
        api.get = lambda path: [{'title': 'Gate PHP security hotfix'}] if '/pulls?' in path else get(path)
        with self.assertRaises(self.a.Refused): api.cve_merged()
        api.get = lambda path: {'number': int(path.rsplit('/', 1)[1]), 'state': 'open', 'merged': False}
        with self.assertRaises(self.a.Refused): api.cve_merged()

    def test_bad_source_or_cached_rollback_source_is_never_executed(self):
        with mock.patch.object(self.a, 'fetch', return_value=b'raise RuntimeError("untrusted")'):
            with self.assertRaises(self.a.Refused): self.a.source_module()
        self.apply()
        folder = self.layout.backup / (self.layout.backup / 'last-backup').read_text().strip()
        (folder / 'new-manager').write_bytes(b'untrusted cache')
        with self.assertRaises(self.a.Refused): self.rollback()

    def test_rollback_does_not_deadlock_on_jobs_queued_for_old_role_pins(self):
        self.apply()
        with mock.patch.object(self.API, 'quiet', side_effect=self.a.Refused('old role job queued')):
            self.rollback()
        self.assertEqual(self.layout.config.read_bytes(), self.initial)

    def test_failed_group_confirmation_stops_before_swap_and_can_recover(self):
        def change_group(*_): self.API.group_failure = True
        with mock.patch.object(self.a, 'manual_group', side_effect=change_group):
            with self.assertRaises(self.a.Refused): self.apply()
        self.assertEqual(self.layout.manager.read_bytes(), self.old)
        self.assertFalse(self.active)
        self.API.group_failure = False
        self.rollback()
        self.assertTrue(self.active)

    def test_startup_failure_keeps_admission_stopped_and_backup_recoverable(self):
        original_command = self.command
        def fail_status(args, **kwargs):
            if self.layout.manager.read_bytes() == self.new and '--property=ExecMainStatus' in args:
                return '1'
            return original_command(args, **kwargs)
        with mock.patch.object(self, 'command', side_effect=fail_status):
            with self.assertRaises(self.a.Refused): self.apply()
        self.assertFalse(self.active)
        self.rollback()
        self.assertEqual(self.layout.manager.read_bytes(), self.old)

    def test_script_refuses_arbitrary_argv_and_has_no_shell_environment_inputs(self):
        script = (ROOT / 'runner/activation/dev54-activate.sh').read_text()
        self.assertIn('/usr/bin/env -i ', script)
        self.assertNotIn('os.environ', script)
        with mock.patch.object(self.a.os, 'geteuid', return_value=0), mock.patch.object(self.a.sys, 'argv', ['script', '--other']):
            with self.assertRaises(self.a.Refused): self.a.main()


if __name__ == '__main__':
    unittest.main()
