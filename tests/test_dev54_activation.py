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
import importlib.util
import urllib.error

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
        # Exercise the transaction against today's real manager in this fake
        # host only. The historical production entrypoint retains its old pin;
        # test_pins_match_reviewed_sources proves it rejects today's bytes.
        self.a.NEW_MANAGER_SHA = self.a.digest(self.new)
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
                        org_repository_ids={r: i + 1 for i, r in enumerate(self.a.AFFECTED) if r != self.a.TESSERA},
                        repositories=[r for r in self.a.AFFECTED if r != self.a.TESSERA], org_execution_roles=roles,
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
            def no_open_cve_hotfixes(self):
                owner.calls.append('cve')
                owner.a.require(not self.blocked, 'open CVE hotfix')
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
        pinned = load_script()
        self.assertEqual(pinned.NEW_MANAGER_SHA,
            '5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3')
        self.assertEqual(pinned.OLD_MANAGER_SHA,
            '54520f176dc5c07adf9493d27f89e549672c27aa6d7fa05f737b6549f0dcb1f3')
        self.assertNotEqual(pinned.NEW_MANAGER_SHA, self.a.digest(self.new))
        with self.assertRaisesRegex(pinned.Refused, 'manager source SHA-256 differs'):
            pinned.module_from_source(self.new)
        # CI intentionally has a depth-one checkout. Keep historical pins without
        # requiring historical Git objects or repinning the production entrypoint.
        self.assertEqual(self.a.SOURCE_COMMIT, 'ea86c62eeac615cb292a78bf50995fed81c051ee')

    def test_original_232d_and_unknown_host_hashes_are_refused_before_mutation(self):
        self.a.OLD_MANAGER_SHA = load_script().OLD_MANAGER_SHA
        digest = self.a.digest
        for installed_hash in ('232d37364c0754bd59aefbf44fee9ba5704def038bbda94514975f857dd61e9f',
                               digest(b'unknown operator installation')):
            # Model the historical host hash without shipping executable old
            # manager bytes or depending on historical Git objects in CI.
            with self.subTest(installed_hash=installed_hash), mock.patch.object(
                    self.a, 'digest', side_effect=lambda data: installed_hash if data == self.old else digest(data)):
                with self.assertRaisesRegex(self.a.Refused, 'unexpected installed manager SHA-256'):
                    self.apply()
                self.assertEqual(self.layout.manager.read_bytes(), self.old)
                self.assertEqual(self.layout.config.read_bytes(), self.initial)
                self.assertFalse(self.layout.backup.exists())
                self.assertEqual(self.calls, [])

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

    def test_read_only_cve_and_quiet_guards_cover_all_five_repos_without_historical_pr_checks(self):
        api = self.a.GitHub.__new__(self.a.GitHub)
        calls = []
        def get(path):
            calls.append(path)
            if '/actions/runs?' in path: return {'total_count': 0, 'workflow_runs': []}
            if '/pulls?' in path: return []
            self.fail('unexpected historical PR check: ' + path)
        api.get = get
        api.no_open_cve_hotfixes(); api.quiet()
        self.assertEqual(len(calls), 15)
        self.assertEqual(set(self.a.AFFECTED), {'Tuinstra-DEV/gate', 'Tuinstra-DEV/tracker',
            'Tuinstra-DEV/notify', 'Tuinstra-DEV/status', 'Tuinstra-DEV/sudoku-tessera'})
        for repo in self.a.AFFECTED:
            self.assertIn(f'/repos/{repo}/pulls?state=open&per_page=100&page=1', calls)
            for status in ('queued', 'in_progress'):
                self.assertIn(f'/repos/{repo}/actions/runs?status={status}&per_page=1', calls)
        for value in ({'total_count': 1, 'workflow_runs': [{}]}, {'total_count': None, 'workflow_runs': []}, {'total_count': False, 'workflow_runs': []}):
            api.get = lambda _: value
            with self.assertRaises(self.a.Refused): api.quiet()
        api.get = lambda path: [{'title': 'Gate PHP security hotfix'}] if '/pulls?' in path else get(path)
        with self.assertRaises(self.a.Refused): api.no_open_cve_hotfixes()
        for value in ({}, [{}] * 100, [None]):
            api.get = lambda _: value
            with self.assertRaises(self.a.Refused): api.no_open_cve_hotfixes()

    def test_tessera_queued_run_or_php_hotfix_also_blocks_activation(self):
        api = self.a.GitHub.__new__(self.a.GitHub)
        api.get = lambda path: ({'total_count': 1, 'workflow_runs': [{}]} if self.a.TESSERA in path
                                else {'total_count': 0, 'workflow_runs': []})
        with self.assertRaises(self.a.Refused): api.quiet()
        for pr in ({'title': 'PHP security hotfix'}, {'head': {'ref': 'bug/CVE-2026-45070'}},
                   {'title': 'IN-46 PHP dependency repair'}):
            api.get = lambda path: [pr] if self.a.TESSERA in path else []
            with self.assertRaises(self.a.Refused): api.no_open_cve_hotfixes()

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

    def diagnostic(self, loader=None):
        spec = importlib.util.spec_from_file_location('dev54_diagnostic', ROOT / 'runner/activation/dev54-diagnose-v2.py')
        diagnostic = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(diagnostic)
        read_file = self.a.read_file
        def read_entrypoint(path, mode=None):
            if path == Path('/usr/local/sbin/dev54-activate.sh'):
                return b'fixture entrypoint', types.SimpleNamespace(st_gid=0)
            return read_file(path, mode)
        sink = io.StringIO()
        with mock.patch.object(self.a, 'read_file', side_effect=read_entrypoint), \
             mock.patch.object(self.a.os, 'geteuid', return_value=0), \
             mock.patch.object(self.a.sys, 'argv', ['script']), \
             mock.patch.object(self.a.sys.stdin, 'isatty', return_value=True), \
             mock.patch.object(self.a.os, 'umask'), mock.patch.object(self.a.signal, 'signal'), \
             contextlib.redirect_stdout(sink):
            status = diagnostic.diagnose(loader=lambda: self.a, inspect=lambda a: diagnostic.inspect_preflight(
                a, self.layout, command=self.command, loader=loader or (lambda: (self.new, manager)), api_factory=self.API))
        return status, sink.getvalue()

    def test_readonly_diagnostic_runs_real_preflight_but_never_locks_writes_backs_up_or_drains(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with mock.patch.object(self.a.fcntl, 'flock', side_effect=AssertionError('real lock')), \
             mock.patch.object(self.a, 'write_new', side_effect=AssertionError('write')), \
             mock.patch.object(self.a, 'private_dir', side_effect=AssertionError('mkdir')):
            status, output = self.diagnostic()
        self.assertEqual(status, 0)
        self.assertEqual(output.splitlines()[0], 'PreflightComplete')
        self.assertTrue(self.groups)
        self.assertEqual({p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        self.assertFalse(self.layout.lock.parent.exists())
        self.assertFalse(self.layout.backup.exists())
        self.assertTrue(self.active)
        self.assertFalse(self.layout.drain.exists())
        self.assertFalse(any(isinstance(c, tuple) and c[:2] == ('systemctl', 'stop') for c in self.calls))

    def test_readonly_diagnostic_reports_class_and_frames_without_secret_exception_or_source(self):
        def fail():
            raise KeyError('synthetic-secret-config-value')
        status, output = self.diagnostic(loader=fail)
        self.assertEqual(status, 1)
        self.assertEqual(output.splitlines()[0], 'KeyError')
        self.assertIn(':fail', output)
        self.assertNotIn('synthetic-secret', output)
        self.assertNotIn('raise KeyError', output)
        self.assertFalse(self.layout.backup.exists())

    def test_http_and_transport_failures_are_safe_refusals_before_backup_or_drain(self):
        for endpoint in ('manager-source', 'open-pr-inventory', 'run-inventory'):
            for status in (403, 404, 429, 500):
                error = urllib.error.HTTPError('https://secret.invalid/sensitive-path', status,
                    'synthetic-secret-message', {'Authorization': 'synthetic-secret-token'}, io.BytesIO(b'synthetic-secret-body'))
                with self.subTest(endpoint=endpoint, status=status), mock.patch.object(
                        self.a.urllib.request.OpenerDirector, 'open', side_effect=error):
                    with self.assertRaisesRegex(self.a.Refused, f'{endpoint} HTTP {status}') as caught:
                        self.a.fetch('https://secret.invalid/sensitive-path', 'synthetic-secret-token', endpoint=endpoint)
                    self.assertNotIn('synthetic-secret', str(caught.exception))
                    self.assertNotIn('sensitive-path', str(caught.exception))
            for error in (urllib.error.URLError('synthetic-secret-reason'), TimeoutError('synthetic-secret-reason')):
                with mock.patch.object(self.a.urllib.request.OpenerDirector, 'open', side_effect=error):
                    with self.assertRaisesRegex(self.a.Refused, f'{endpoint} transport failure') as caught:
                        self.a.fetch('https://secret.invalid', endpoint=endpoint)
                    self.assertNotIn('synthetic-secret', str(caught.exception))

    def test_unexpected_failure_report_has_only_exception_class_and_fixed_step(self):
        self.a.step('configuration')
        sink = io.StringIO()
        with contextlib.redirect_stderr(sink):
            self.a.report_failure(KeyError('synthetic-secret-config-value'))
        self.assertEqual(sink.getvalue(), 'DEV-54 failed: KeyError at step configuration.\n')

    def test_repo_visibility_denial_uses_existing_org_credential_for_the_identical_get(self):
        api = self.a.GitHub.__new__(self.a.GitHub)
        api.repo_token, api.org_token = 'synthetic-repo-token', 'synthetic-org-token'
        for repo in self.a.AFFECTED:
            for endpoint in ('pulls?state=open&per_page=100&page=1', 'actions/runs?status=queued&per_page=1'):
                path = f'/repos/{repo}/{endpoint}'
                requests = []
                def opened(request, **kwargs):
                    requests.append(request)
                    if request.get_header('Authorization') == 'Bearer synthetic-repo-token':
                        raise urllib.error.HTTPError(request.full_url, 404, 'synthetic-secret', {}, io.BytesIO())
                    return contextlib.nullcontext(types.SimpleNamespace(status=200, read=lambda _: b'[]'))
                with mock.patch.object(self.a.urllib.request.OpenerDirector, 'open', side_effect=opened):
                    self.assertEqual(api.get(path), [])
                self.assertEqual([r.full_url for r in requests], ['https://api.github.com' + path] * 2)
                self.assertEqual([r.get_method() for r in requests], ['GET', 'GET'])
                self.assertEqual([r.get_header('Authorization') for r in requests],
                    ['Bearer synthetic-repo-token', 'Bearer synthetic-org-token'])

    def test_both_credential_denials_refuse_with_repo_and_status_without_secret_data(self):
        api = self.a.GitHub.__new__(self.a.GitHub)
        api.repo_token, api.org_token = 'synthetic-repo-token', 'synthetic-org-token'
        path = f'/repos/{self.a.TESSERA}/pulls?state=open&per_page=100&page=1'
        def denied(request, **kwargs):
            raise urllib.error.HTTPError(request.full_url, 403, 'synthetic-secret', {}, io.BytesIO())
        with mock.patch.object(self.a.urllib.request.OpenerDirector, 'open', side_effect=denied) as opened:
            with self.assertRaisesRegex(self.a.Refused, f'open-pr-inventory {self.a.TESSERA} HTTP 403') as caught:
                api.get(path)
            self.assertEqual(opened.call_count, 2)
            self.assertNotIn('synthetic-', str(caught.exception))

    def test_rate_limits_server_and_transport_failures_never_trigger_credential_retry(self):
        api = self.a.GitHub.__new__(self.a.GitHub)
        api.repo_token, api.org_token = 'synthetic-repo-token', 'synthetic-org-token'
        for error in (urllib.error.HTTPError('https://secret.invalid', 429, 'synthetic-secret', {}, io.BytesIO()),
                      urllib.error.HTTPError('https://secret.invalid', 500, 'synthetic-secret', {}, io.BytesIO()),
                      urllib.error.HTTPError('https://secret.invalid', 403, 'synthetic-secret',
                          {'X-RateLimit-Remaining': '0'}, io.BytesIO()),
                      urllib.error.HTTPError('https://secret.invalid', 403, 'synthetic-secret',
                          {'Retry-After': '30'}, io.BytesIO()),
                      urllib.error.URLError('synthetic-secret'), TimeoutError('synthetic-secret')):
            with mock.patch.object(self.a.urllib.request.OpenerDirector, 'open', side_effect=error) as opened:
                with self.assertRaises(self.a.Refused): api.get(f'/repos/{self.a.TESSERA}/actions/runs?status=queued')
                self.assertEqual(opened.call_count, 1)

    def test_readonly_http_diagnostic_includes_known_repo_status_but_not_url_body_or_token(self):
        def fail():
            raise urllib.error.HTTPError('https://api.github.com/repos/Tuinstra-DEV/sudoku-tessera/pulls?secret=value',
                404, 'synthetic-secret-message', {'Authorization': 'synthetic-secret-token'}, io.BytesIO(b'synthetic-secret-body'))
        status, output = self.diagnostic(loader=fail)
        self.assertEqual(status, 1)
        self.assertEqual(output.splitlines()[0],
            f'HTTPError endpoint=open-pr-inventory repository={self.a.TESSERA} status=404')
        self.assertNotIn('synthetic-secret', output)
        self.assertNotIn('secret=value', output)
        self.assertNotIn('https://', output)

    def test_org_group_wrapped_http_errors_are_safe_refusals_and_policy_failures_still_refuse(self):
        api = self.a.GitHub.__new__(self.a.GitHub)
        api.module, api.org_token = manager, 'synthetic-org-token'
        def denied(*_):
            try:
                raise urllib.error.HTTPError('https://secret.invalid', 403, 'synthetic-secret', {}, io.BytesIO())
            except urllib.error.HTTPError as error:
                self.addCleanup(error.close)
                raise manager.RunnerError('synthetic-secret-wrapped-message') from error
        with mock.patch.object(manager.GitHubClient, 'verify_org_group', side_effect=denied):
            with self.assertRaisesRegex(self.a.Refused, 'runner-group HTTP 403') as caught:
                api.group(self.cfg)
            self.assertNotIn('synthetic-', str(caught.exception))
        with mock.patch.object(manager.GitHubClient, 'verify_org_group', side_effect=manager.RunnerError('synthetic-secret')):
            with self.assertRaisesRegex(self.a.Refused, 'runner-group policy/API validation failed'):
                api.group(self.cfg)

    def test_same_credential_is_not_retried_and_successful_primary_never_uses_org_token(self):
        api = self.a.GitHub.__new__(self.a.GitHub)
        api.repo_token = api.org_token = 'same-synthetic-token'
        error = urllib.error.HTTPError('https://secret.invalid', 404, 'synthetic-secret', {}, io.BytesIO())
        with mock.patch.object(self.a.urllib.request.OpenerDirector, 'open', side_effect=error) as opened:
            with self.assertRaises(self.a.Refused): api.get(f'/repos/{self.a.TESSERA}/pulls?state=open')
            self.assertEqual(opened.call_count, 1)
        api.org_token = 'different-synthetic-token'
        response = contextlib.nullcontext(types.SimpleNamespace(status=200, read=lambda _: b'[]'))
        with mock.patch.object(self.a.urllib.request.OpenerDirector, 'open', return_value=response) as opened:
            self.assertEqual(api.get(f'/repos/{self.a.TESSERA}/pulls?state=open'), [])
            self.assertEqual(opened.call_count, 1)

    def test_network_refusal_during_preflight_never_creates_backup_or_stops_manager(self):
        def fail():
            with mock.patch.object(self.a.urllib.request.OpenerDirector, 'open', side_effect=urllib.error.URLError('synthetic-secret')):
                return self.a.source_module()
        with self.assertRaisesRegex(self.a.Refused, 'manager-source transport failure'):
            self.a.apply(self.layout, self.command, loader=fail, api_factory=self.API)
        self.assertFalse(self.layout.backup.exists())
        self.assertFalse(self.layout.drain.exists())
        self.assertTrue(self.active)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
