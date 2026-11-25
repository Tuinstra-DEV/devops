"""Operator preflight reads only, binds the baseline, and emits no credentials."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'docs/runner/proposals/dev54-gate-permanent/integration-preflight.py'
spec = importlib.util.spec_from_file_location('gate_preflight', PATH)
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)


class PermanentPreflightTests(unittest.TestCase):
    def check(self, drift=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = root / 'manager'; manager.write_bytes(b'source')
            cfg = root / 'manager.toml'; cfg.write_text('org_runner_group_id=4\n[org_repository_ids]\n"Tuinstra-DEV/gate"=1273386924\n')
            helper = root / 'helper'; helper.write_text('PROFILE_RESOURCES = {"heavy": (4, 6144), "medium": (2, 3072)}')
            mem = root / 'meminfo'; mem.write_text('MemAvailable: 9000000 kB\n')
            state = root / 'state'; state.mkdir(mode=0o700)
            group = {'id':4, 'visibility':'selected','restricted_to_workflows':True,'allows_public_repositories':False,'selected_workflows':['existing@'+'a'*40]}
            repos = {'total_count':1,'repositories':[{'id':1273386924,'full_name':'Tuinstra-DEV/gate'}]}
            for name, value in [('manager-manifest.json',{'source_sha256':'bad' if drift else hashlib.sha256(b'source').hexdigest()}),('group-state.json',group),('group-before.json',group),('repositories-state.json',repos),('repositories-before.json',{'Tuinstra-DEV/gate':1273386924})]:
                (root/name).write_text(json.dumps(value))
            client = mock.Mock(); client.request.return_value = {}
            effective = mock.Mock(); effective.configured_org_workflow_refs.return_value = group['selected_workflows']; effective.GitHubClient.return_value = client
            checker = mock.Mock(); checker.loaded_manager_credential.return_value = 'SECRET_SENTINEL'
            checker.gate_candidate_image.return_value = (root/'candidate', {'sha256':'a'*64})
            checker.candidate_image_php_check.return_value = {'pdo_mysql':True,'pdo_sqlite':True}
            def loaded(path, name): return effective if path == manager else checker
            def command(args, **kwargs):
                if args[:2] == ['qemu-img','info']: return '{}'
                self.assertEqual(args, ['virsh','-c','qemu:///system','list','--uuid']); return ''
            lstat = Path.lstat
            def root_stat(path):
                info = lstat(path)
                return types.SimpleNamespace(st_uid=0, st_mode=info.st_mode)
            with mock.patch.multiple(probe, PACKAGE=root, MANAGER=manager, CONFIG=cfg, HELPER=helper, STATE=state, MEMINFO=mem), \
                    mock.patch.object(probe.os,'geteuid',return_value=0), mock.patch.object(probe,'module',side_effect=loaded), \
                    mock.patch.object(Path,'lstat',root_stat), mock.patch.object(probe.subprocess,'check_output',side_effect=command), \
                    mock.patch.object(probe.shutil,'disk_usage',return_value=types.SimpleNamespace(free=80*1024**3)):
                result = probe.inspect()
            self.assertTrue(result['all_checks_green'])
            self.assertTrue(result['runner_window']['empty'])
            self.assertNotIn('SECRET_SENTINEL',json.dumps(result))
            for call in client.request.call_args_list: self.assertEqual(call.args[0],'GET')
            checker.loaded_manager_credential.assert_called_once_with(mock.ANY,effective)
            return result

    def test_readiness_is_get_only_and_never_serializes_credential(self): self.check()
    def test_baseline_drift_refuses_before_loading_credential(self):
        with self.assertRaisesRegex(ValueError,'effective_manager_baseline_changed'): self.check(drift=True)

    def test_wrapper_preserves_existing_ssh_and_operator_boundary(self):
        source = (PATH.parent/'run-integration-preflight.sh').read_text()
        self.assertIn('mtuinstra@sanctuary.tuinstra.dev',source)
        self.assertIn('sudo /usr/bin/python3 -B',source)
        for forbidden in ['StrictHostKeyChecking=no','sudoers','restart','registration-token','jitconfig']:
            self.assertNotIn(forbidden,source)
