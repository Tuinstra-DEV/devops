import hashlib
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
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


def real_managers(root):
    manifest=json.loads((PROPOSAL/'manager-manifest.json').read_text())
    source=gzip.decompress((ROOT/'runner/tests/fixtures/gate-permanent/manager-5404.py.gz').read_bytes())
    before=root/'before.py';before.write_bytes(source)
    target=root/'runner/manager/ci_runner_manager.py';target.parent.mkdir(parents=True);target.write_bytes(source)
    subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(PROPOSAL/'manager-effective.patch')],cwd=root,check=True,capture_output=True)
    assert hashlib.sha256(source).hexdigest()==manifest['source_sha256']
    assert hashlib.sha256(target.read_bytes()).hexdigest()==manifest['target_sha256']
    return permanent.load(before,'real_before_activation'),permanent.load(target,'real_after_activation'),target


class ActualPolicyActivationTests(unittest.TestCase):
    """Exercise both real manager classes; fake only host operations and HTTP GETs."""
    def test_activation_reads_new_policy_with_new_manager_and_same_credential(self):
        self.exercise()

    def test_group_repository_or_http_failure_still_rolls_back(self):
        for fault in ('group','repositories','http403'):
            with self.subTest(fault=fault):self.exercise(fault)

    def exercise(self,fault=None):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);old,new,staged=real_managers(root)
            refs=json.loads((OLD/'group-before.json').read_text())['selected_workflows']
            trusted=[ref for ref in refs if '/reusable-trusted-verification.yml@' in ref]
            trusted.remove(old.LEGACY_METADATA_WORKFLOW_REF);trusted.insert(3,old.LEGACY_METADATA_WORKFLOW_REF)
            repositories=json.loads((OLD/'repositories-before.json').read_text())
            original=('org_routing_enabled=true\npool_mode="four"\norg_runner_group_id=4\n'
                      'github_token_file="/run/credentials/ci-runner-manager.service/github_token"\n'
                      'org_github_token_file='+json.dumps(old.ORG_CREDENTIAL_PATH)+'\n'
                      'org_workflow_refs='+json.dumps(trusted)+'\n[org_repository_ids]\n'+
                      '\n'.join(json.dumps(repo)+'='+str(value) for repo,value in repositories.items())+'\n')
            for role,ref,event in [('metadata-preflight',trusted[-1],'pull_request'),('pr-security-producer',refs[0],'pull_request_target')]:
                original+='[org_execution_roles.'+role+']\nworkflow_ref='+json.dumps(ref)+'\nrepositories=["Tuinstra-DEV/gate"]\nevents=['+json.dumps(event)+']\nactors=[{login="marcel-tuinstra",type="User"}]\n'
            original=original.encode();cfg=tomllib.loads(original.decode());old.validate_org_config(cfg)
            pin=json.loads((PROPOSAL/'manager-manifest.json').read_text())['workflow_pin']
            after_cfg=tomllib.loads(permanent.render_roles(original,pin).decode())
            new.validate_org_config(after_cfg)
            old_probe=old.GitHubClient('SECRET_SENTINEL')
            with mock.patch.object(old_probe,'request') as unused:
                with self.assertRaisesRegex(old.RunnerError,'organization execution roles are invalid'):
                    old_probe.verify_org_group(after_cfg)
                unused.assert_not_called()
            group={'id':4,'name':old.ORG_GROUP_NAME,'visibility':'selected','default':False,'inherited':False,
                   'allows_public_repositories':False,'restricted_to_workflows':True,'selected_workflows':refs.copy()}
            repos={'total_count':len(repositories),'repositories':[{'full_name':repo,'id':value,'private':True} for repo,value in repositories.items()]}
            calls=[]
            def request(client,method,path,*args):
                self.assertEqual(method,'GET');calls.append((type(client),len(group['selected_workflows'])))
                if len(group['selected_workflows'])==8 and fault=='http403':raise new.RunnerError('GitHub GET HTTP 403 SECRET_SENTINEL')
                if path.endswith('/repositories?per_page=100&page=1'):
                    return {'total_count':0,'repositories':[]} if fault=='repositories' and len(group['selected_workflows'])==8 else repos
                if fault=='group' and len(group['selected_workflows'])==8:return {**group,'selected_workflows':refs}
                self.assertTrue(path.endswith('/runner-groups/4'));return group
            manager=root/'manager';manager.write_bytes(gzip.decompress((ROOT/'runner/tests/fixtures/gate-permanent/manager-5404.py.gz').read_bytes()));manager.chmod(0o755)
            config=root/'config';config.write_bytes(original);config.chmod(0o600)
            backup=root/'backup';backup.mkdir();before={}
            for path,key,name in [(manager,'manager','manager.before'),(config,'config','manager.toml.before')]:
                info=path.stat();before.update({key+'_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),key+'_uid':info.st_uid,key+'_gid':info.st_gid,key+'_mode':info.st_mode&0o7777})
                (backup/name).write_bytes(path.read_bytes());(backup/name).chmod(0o600)
            (backup/'before.json').write_text(json.dumps(before));image=root/'image';image.write_bytes(b'image')
            after_refs=refs+list(dict.fromkeys(after_cfg['org_execution_roles'][role]['workflow_ref'] for role in permanent.ROLES))
            (root/'plan.json').write_text(json.dumps({'runner_group_before':group.copy(),'runner_group_after':{'selected_workflows':after_refs},'repository_ids_unchanged':list(repositories.values())}))
            (root/'request.json').write_text('{}')
            class Blocked(Exception):
                def __init__(self,code,http_status=None):self.code=code;self.http_status=http_status
            base=mock.Mock();base.Blocked=Blocked;base.JOURNAL=root/'journal';base.STATUS=root/'status';base.MANAGER=manager;base.CONFIG=config;base.STAGED_MANAGER=staged
            base.OLD_MANAGER_HASH=hashlib.sha256(manager.read_bytes()).hexdigest();base.NEW_MANAGER_HASH=hashlib.sha256(staged.read_bytes()).hexdigest()
            base.sha256_file.side_effect=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
            base.secure_regular.side_effect=lambda p,*_:p.read_bytes();base.parse_manager_config.side_effect=lambda raw:tomllib.loads(raw.decode())
            base.load_module.side_effect=lambda path,*_:old if path==manager else new
            old_client=old.GitHubClient('SECRET_SENTINEL');base.existing_manager_client.return_value=('SECRET_SENTINEL',old_client)
            checker=mock.Mock();base.CANDIDATE_HASH='image';checker.gate_candidate_image.return_value=(image,{'sha256':'image'});base.load_operator_checker.return_value=checker
            base.CANDIDATE_SOURCE=image;base.NEW_IMAGE=root/'sealed';base.make_backup.return_value=(backup,before)
            base.load_backup_before.return_value=before;base.write_json.side_effect=lambda path,value,*_:path.write_text(json.dumps(value))
            base.read_group.side_effect=lambda *_:(group,repos);base.group_policy_matches.return_value=True;base.repository_map.side_effect=lambda data:{r['full_name']:r['id'] for r in data['repositories']}
            base.patch_group.side_effect=lambda client,selected:group.update(selected_workflows=selected)
            base.prepare_sealed_candidate.return_value={'mode':'preexisting'};base.read_staged_manager.return_value=staged.read_bytes()
            def write(path,data,uid,gid,mode):path.write_bytes(data);path.chmod(mode)
            base.atomic_write.side_effect=write;base.service_state.return_value={'ActiveState':'active'}
            with mock.patch.object(old.GitHubClient,'request',autospec=True,side_effect=request),mock.patch.object(new.GitHubClient,'request',autospec=True,side_effect=request),mock.patch.object(permanent,'check_merged'):
                if fault:
                    with self.assertRaises(Blocked) as error:
                        permanent.activate(base,{'workflow_pin':pin,'target_sha256':base.NEW_MANAGER_HASH},root)
                    self.assertEqual(error.exception.code,'activated_group_readback_failed')
                    self.assertEqual(error.exception.http_status,403 if fault=='http403' else None)
                    self.assertNotIn('SECRET_SENTINEL',str(error.exception))
                    base.rollback_stopped_transaction.assert_called_once()
                    return
                result=permanent.activate(base,{'workflow_pin':pin,'target_sha256':base.NEW_MANAGER_HASH},root)
            self.assertEqual(result['phase'],'active');self.assertEqual(len(group['selected_workflows']),8)
            self.assertTrue(any(count==8 for _,count in calls))
            self.assertTrue(all(kind is new.GitHubClient for kind,count in calls if count==8))
            base.existing_manager_client.assert_called_once();base.rollback_stopped_transaction.assert_not_called()


class CompletedRecoveryArchiveTests(unittest.TestCase):
    def exercise(self,fault=None):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);backup=root/'backup';backup.mkdir()
            state={'phase':'active' if fault=='active' else 'rolled_back','backup':str(backup),'before':{'known':'baseline'}}
            journal=root/'active.json';journal.write_text(json.dumps(state));journal.chmod(0o600)
            archived=backup/'completed-activation-journal.json'
            if fault=='archive_exists':archived.write_bytes(b'preserve existing archive')
            class Blocked(Exception):
                def __init__(self,code):self.code=code
            base=mock.Mock();base.Blocked=Blocked;base.JOURNAL=journal
            base.secure_regular.side_effect=lambda path:path.read_bytes()
            base.load_backup_before.return_value=state['before'];base.service_state.return_value={'ActiveState':'inactive' if fault=='service' else 'active'}
            base.parse_manager_config.return_value={};base.existing_manager_client.return_value=('SECRET_SENTINEL',mock.Mock())
            base.finish_already_restored_rollback.return_value=fault!='readback'
            if fault=='image':base.verify_candidate_and_source.side_effect=Blocked('image_changed')
            if fault in {'active','archive_exists','service','readback','image'}:
                with self.assertRaises(Blocked):permanent.archive_completed_rollback(base,root)
                self.assertEqual(json.loads(journal.read_text()),state)
                if fault=='archive_exists':self.assertEqual(archived.read_bytes(),b'preserve existing archive')
                else:self.assertFalse(archived.exists())
                base.safe_command.assert_not_called();return
            original=journal.read_bytes()
            if fault=='fsync':
                with mock.patch.object(permanent.os,'fsync',side_effect=OSError('interrupted after rename')):
                    with self.assertRaises(OSError):permanent.archive_completed_rollback(base,root)
            else:permanent.archive_completed_rollback(base,root)
            self.assertFalse(journal.exists());self.assertEqual(archived.read_bytes(),original)
            self.assertEqual(archived.stat().st_mode&0o7777,0o600)
            base.verify_candidate_and_source.assert_called_once();base.finish_already_restored_rollback.assert_called_once()
            # A crash after the atomic rename cannot lose the completed recovery.
            permanent.archive_completed_rollback(base,root)
            self.assertEqual(archived.read_bytes(),original);base.safe_command.assert_not_called()

    def test_verified_completed_recovery_is_preserved_and_retry_is_idempotent(self):self.exercise()
    def test_interruption_after_archive_preserves_evidence(self):self.exercise('fsync')
    def test_incomplete_drifted_or_unreadable_recovery_is_never_archived(self):
        for fault in ('active','archive_exists','service','readback','image'):
            with self.subTest(fault=fault):self.exercise(fault)

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
            candidate_client=mock.Mock();new.GitHubClient=mock.Mock(return_value=candidate_client)
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
            new.GitHubClient.assert_called_once_with('SECRET_SENTINEL',api_url='https://api.github.com')
            candidate_client.verify_org_group.assert_called_once()
            client.verify_org_group.assert_not_called()
