import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
PROPOSAL=ROOT/'docs/runner/proposals/dev54-gate-permanent'
OLD=ROOT/'docs/runner/proposals/dev54-gate-backend'

def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

permanent=module(PROPOSAL/'permanent-transaction.py','permanent_activation_test')
prior=module(OLD/'gate-pilot-transaction-tests.py','existing_gate_recovery_tests')

class RoleRenderingTests(unittest.TestCase):
    def test_three_roles_work_with_inline_and_table_configs_without_other_changes(self):
        for data in [b'credential="private-sentinel"\norg_execution_roles = {}\n',b'credential="private-sentinel"\n[org_execution_roles.other]\nworkflow_ref="old"\n']:
            before=tomllib.loads(data.decode());out=permanent.render_roles(data,'a'*40);after=tomllib.loads(out.decode())
            roles=after.pop('org_execution_roles');self.assertEqual(after,{k:v for k,v in before.items() if k!='org_execution_roles'})
            self.assertEqual(set(roles),set(before['org_execution_roles'])|set(permanent.ROLES))
            refs={roles[role]['workflow_ref'] for role in permanent.ROLES};self.assertEqual(len(refs),2)
            for role in permanent.ROLES:
                self.assertEqual(roles[role]['events'],['pull_request']);self.assertEqual(roles[role]['repositories'],['Tuinstra-DEV/gate'])
            with self.assertRaisesRegex(ValueError,'already_present'):permanent.render_roles(out,'a'*40)
    def test_engine_is_the_exact_corrected_rollback_and_no_old_pilot_activation_is_called(self):
        self.assertEqual(hashlib.sha256((OLD/'gate-pilot-transaction.py').read_bytes()).hexdigest(),permanent.BASE_SHA256)
        source=(PROPOSAL/'permanent-transaction.py').read_text()
        self.assertIn('base.rollback_only(package)',source);self.assertIn('verify_recovery_inputs(base,state,live_before=True)',source)
        self.assertNotIn('base.activate(',source)
    def test_unmerged_or_changed_pr_cannot_activate(self):
        client=mock.Mock();client.request.return_value={'merged':False}
        with self.assertRaisesRegex(ValueError,'reviewed_pr_merge_differs'):permanent.check_merged(client,{},'a'*40)

class PermanentRollbackInterruptionTests(prior.RollbackInterruptionTests):
    """Same real-byte/symlink checkpoints as 91c9cf3, now with six to eight refs."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        before=json.loads((OLD/'group-before.json').read_text())
        after={**before,'selected_workflows':before['selected_workflows']+[
            'Tuinstra-DEV/devops/.github/workflows/reusable-gate-ci.yml@'+'a'*40,
            'Tuinstra-DEV/devops/.github/workflows/reusable-gate-release-ci.yml@'+'a'*40]}
        for name,value in [('group-before.json',before),('group-after.json',after)]: (root/name).write_text(json.dumps(value))
        (root/'repositories-before.json').write_bytes((OLD/'repositories-before.json').read_bytes())
        self.patch=mock.patch.object(prior,'SCRIPT',root/'placeholder.py');self.patch.start();self.addCleanup(self.patch.stop)
        location=mock.patch.object(prior,'__file__',str(root/'placeholder-tests.py'));location.start();self.addCleanup(location.stop)

class PermanentActivationOrderingTests(unittest.TestCase):
    def test_image_or_helper_drift_after_wait_is_rejected_before_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pin='a'*40
            refs=['old@'+str(i)*40 for i in range(6)]
            after=refs+[f'Tuinstra-DEV/devops/.github/workflows/{w}@{pin}' for w in dict.fromkeys(permanent.ROLES.values())]
            (root/'plan.json').write_text(json.dumps({'runner_group_before':{'selected_workflows':refs},'runner_group_after':{'selected_workflows':after}}))
            (root/'request.json').write_text('{}')
            class Blocked(Exception):
                def __init__(self,code):self.code=code
            base=mock.Mock();base.Blocked=Blocked;base.JOURNAL=root/'absent-journal'
            base.MANAGER=root/'manager';base.CONFIG=root/'config';base.STAGED_MANAGER=root/'candidate'
            base.OLD_MANAGER_HASH='old';base.sha256_file.return_value='old'
            base.secure_regular.return_value=b'org_execution_roles = {}\n'
            base.parse_manager_config.side_effect=lambda raw:tomllib.loads(raw.decode())
            old=mock.Mock();old.configured_org_workflow_refs.return_value=refs
            new=mock.Mock();new.configured_org_workflow_refs.return_value=after
            base.load_module.side_effect=[old,new]
            base.existing_manager_client.return_value=('SECRET_SENTINEL',mock.Mock())
            base.CANDIDATE_HASH='image';checker=mock.Mock()
            checker.gate_candidate_image.return_value=(root/'image',{'sha256':'image'})
            base.load_operator_checker.return_value=checker
            # The first check succeeds; the empty-window wait permits external drift.
            base.verify_candidate_and_source.side_effect=[None,Blocked('baseline_drift')]
            base.make_backup.side_effect=AssertionError('drift must not become the recovery baseline')
            with mock.patch.object(permanent,'check_merged'),mock.patch.object(permanent,'validate_policy'):
                with self.assertRaises(Blocked) as error:
                    permanent.activate(base,{'workflow_pin':pin},root)
            self.assertEqual(error.exception.code,'baseline_drift')
            base.make_backup.assert_not_called();base.safe_command.assert_not_called()

    def test_backup_readback_precedes_stop_and_empty_window_race_restarts_original_service(self):
        from types import SimpleNamespace
        import hashlib
        import stat
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);package=root/'package';package.mkdir();backup=root/'backup';backup.mkdir()
            manager=root/'manager';manager.write_bytes(b'original manager');manager.chmod(0o755)
            config=root/'config';original=b'org_execution_roles = {}\n[org_repository_ids]\n"Tuinstra-DEV/gate"=1273386924\n';config.write_bytes(original);config.chmod(0o600)
            candidate=root/'candidate';candidate.write_bytes(b'image')
            refs=['old@'+str(i)*40 for i in range(6)];pin='a'*40
            after_refs=refs+[f'Tuinstra-DEV/devops/.github/workflows/{w}@{pin}' for w in dict.fromkeys(permanent.ROLES.values())]
            (package/'plan.json').write_text(json.dumps({'runner_group_before':{'selected_workflows':refs},'runner_group_after':{'selected_workflows':after_refs}}))
            (package/'request.json').write_text('{}')
            before={}
            for path,key in [(manager,'manager'),(config,'config')]:
                info=path.stat();before.update({key+'_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),key+'_uid':info.st_uid,key+'_gid':info.st_gid,key+'_mode':stat.S_IMODE(info.st_mode)})
                (backup/('manager.before' if key=='manager' else 'manager.toml.before')).write_bytes(path.read_bytes());(backup/('manager.before' if key=='manager' else 'manager.toml.before')).chmod(0o600)
            (backup/'before.json').write_text(json.dumps(before))
            events=[];service={'active':True};readback={'done':False};stops={'count':0}
            class Blocked(Exception):
                def __init__(self,code):self.code=code
            base=mock.Mock();base.Blocked=Blocked;base.JOURNAL=root/'active.json';base.STATUS=package/'status.json';base.CONFIG=config;base.MANAGER=manager
            base.verify_candidate_and_source.side_effect=lambda:events.append('baseline_running' if service['active'] else 'baseline_stopped')
            base.CANDIDATE_SOURCE=candidate;base.NEW_IMAGE=root/'sealed';base.NEW_IMAGE.write_bytes(b'image');base.CANDIDATE_HASH='b'*64
            base.OLD_MANAGER_HASH=hashlib.sha256(manager.read_bytes()).hexdigest();base.NEW_MANAGER_HASH=hashlib.sha256(b'new manager').hexdigest();base.SERVICE='ci-runner-manager.service';base.MAX_WAIT_SECONDS=1
            base.sha256_file.side_effect=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            base.secure_regular.side_effect=lambda p,*_:p.read_bytes()
            base.parse_manager_config.side_effect=lambda b:permanent.tomllib.loads(b.decode())
            old=SimpleNamespace(validate_org_config=lambda c:None,configured_org_workflow_refs=lambda c:refs)
            new=SimpleNamespace(validate_org_config=lambda c:None,configured_org_workflow_refs=lambda c:list(dict.fromkeys(refs+[r['workflow_ref'] for r in c['org_execution_roles'].values()])))
            base.STAGED_MANAGER=root/'new.py';base.load_module.side_effect=lambda p,*_:old if p==manager else new
            checker=mock.Mock();checker.gate_candidate_image.return_value=(candidate,{'sha256':base.CANDIDATE_HASH});base.load_operator_checker.return_value=checker
            client=mock.Mock();base.existing_manager_client.return_value=('SECRET_SENTINEL',client)
            base.make_backup.return_value=(backup,before)
            def write_json(path,value,*_):path.write_text(json.dumps(value))
            base.write_json.side_effect=write_json
            base.load_backup_before.side_effect=lambda p,state:json.loads((p/'before.json').read_text())
            def verified(metadata):
                self.assertEqual(metadata,before);self.assertEqual(config.read_bytes(),original);readback['done']=True;events.append('readback')
            base.verify_restored_files.side_effect=verified
            def command(args):
                if args[1]=='stop':
                    self.assertTrue(readback['done']);stops['count']+=1;service['active']=False;events.append('stop')
                else:service['active']=True;events.append('start')
            base.safe_command.side_effect=command
            def empty():
                if stops['count']==1:raise Blocked('runner_window_not_empty')
            base.ensure_empty.side_effect=empty
            base.prepare_sealed_candidate.return_value={'mode':'preexisting'}
            def group_patch(*_):
                self.assertEqual(stops['count'],2);self.assertFalse(service['active']);events.append('group_patch')
            base.patch_group.side_effect=group_patch
            base.read_staged_manager.return_value=b'new manager'
            def atomic(path,data,uid,gid,mode):path.write_bytes(data);path.chmod(mode)
            base.atomic_write.side_effect=atomic
            with mock.patch.object(permanent,'check_merged'),mock.patch.object(permanent,'validate_policy'):
                result=permanent.activate(base,{'workflow_pin':pin,'target_sha256':base.NEW_MANAGER_HASH},package)
            self.assertEqual(result['phase'],'active');self.assertEqual(result['group_refs'],8)
            self.assertEqual(events[:3],['baseline_running','baseline_running','readback'])
            self.assertEqual([e for e in events if not e.startswith('baseline_')][:5],['readback','stop','start','stop','group_patch'])
            self.assertGreaterEqual(base.verify_candidate_and_source.call_count,5)
            self.assertLess(events.index('baseline_stopped'),events.index('group_patch'))
            self.assertEqual(manager.read_bytes(),b'new manager')
            self.assertEqual(set(permanent.tomllib.loads(config.read_text())['org_execution_roles']),set(permanent.ROLES))
            self.assertEqual(stat.S_IMODE(config.stat().st_mode),0o600)
