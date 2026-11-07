"""Offline transaction tests; never run sudo, gh, virsh, systemctl or a network."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'scripts/runner-controlled-cutover.py'

def load():
    spec=importlib.util.spec_from_file_location('controlled_cutover',SCRIPT)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def value(v):
    if type(v) is bool: return str(v).lower()
    if type(v) in (str,int): return json.dumps(v)
    if type(v) is list: return '['+', '.join(value(x) for x in v)+']'
    if type(v) is dict: return '{ '+', '.join(json.dumps(k)+' = '+value(x) for k,x in v.items())+' }'
    raise AssertionError(type(v))

def serialize(v): return ('\n'.join(json.dumps(k)+' = '+value(x) for k,x in v.items())+'\n').encode()

def historical(path):
    # Reconstruct exact pinned old bytes even in a shallow/no-history CI checkout.
    # The immutable hashes checked by each fixture detect any source/patch drift.
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        for source in ('runner/manager/ci_runner_manager.py','runner/host-helper/ci_runner_host_helper.py'):
            target=root/source;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/source).read_bytes())
        subprocess.run(['git','apply',str(ROOT/'tests/fixtures/runner-cutover-before.patch')],cwd=root,capture_output=True,check=True)
        return (root/path).read_bytes()

class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.a=load(); a=self.a
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve(); a.UID=os.getuid(); a.ANCHOR=self.root
        for name in ('MANAGER','CONFIG','UNIT','HELPER','DROPIN','PAUSE','STATE','OVERLAY','STAGE','BACKUP','OLD_BACKUP','OLD_PAUSE','LOCK'):
            setattr(a,name,self.root/name.lower())
        a.JOURNAL=a.BACKUP/'journal.json'
        for p in (a.STATE,a.OVERLAY,a.STAGE): p.mkdir(mode=0o700)
        a.LOCK.write_bytes(b''); a.LOCK.chmod(0o600)
        a.MANAGER.write_bytes(historical('runner/manager/ci_runner_manager.py'))
        a.HELPER.write_bytes(historical('runner/host-helper/ci_runner_host_helper.py'))
        a.UNIT.write_bytes((ROOT/'runner/systemd/ci-runner-manager.service').read_bytes())
        (a.STAGE/'manager.new').write_bytes((ROOT/'runner/manager/ci_runner_manager.py').read_bytes())
        (a.STAGE/'helper.new').write_bytes((ROOT/'runner/host-helper/ci_runner_host_helper.py').read_bytes())
        self.assertEqual(a.sha(a.MANAGER.read_bytes()),a.MANAGER_SHA)
        self.assertEqual(a.sha(a.HELPER.read_bytes()),a.HELPER_SHA)
        self.assertEqual(a.sha((a.STAGE/'manager.new').read_bytes()),a.NEW_MANAGER_SHA)
        self.assertEqual(a.sha((a.STAGE/'helper.new').read_bytes()),a.NEW_HELPER_SHA)
        a.DROPIN.write_bytes(b'[Service]\nLoadCredential=org_github_token:/etc/ci-runner/org-github.token\n')
        cfg=tomllib.loads((ROOT/'runner/config/manager.toml').read_text())
        cfg.update(pool_mode='four',max_concurrency=4,medium_runner_label='trusted-medium',medium_runner_vcpus=2,
                   medium_runner_memory_mib=3072,max_heavy=2,max_medium=2,heavy_disk_reservation_gib=24,
                   medium_disk_reservation_gib=4,storage_mode='bounded-loop',org_routing_enabled=True,
                   org_runner_group_id=4,org_github_token_file='/run/credentials/ci-runner-manager.service/org_github_token',
                   org_repository_ids=a.IDS,repositories=list(a.IDS),org_workflow_refs=a.BEFORE[:-1],
                   state_dir=str(a.STATE),lock_file=str(a.LOCK),overlay_root=str(a.OVERLAY),
                   org_execution_roles={role:{'workflow_ref':ref,'repositories':['Tuinstra-DEV/gate','Tuinstra-DEV/tracker'],
                        'events':[event],'actors':[{'login':'marcel-tuinstra','type':'User'}]} for role,ref,event in
                        [('metadata-preflight',a.TRUSTED+a.PINS[-1],'pull_request'),('pr-security-producer',a.PRODUCER,'pull_request_target')]})
        self.cfg=cfg; self.original=serialize(cfg); a.CONFIG.write_bytes(self.original); a.CONFIG.chmod(0o640)
        self.binary=a.MANAGER.read_bytes(); self.helper_binary=a.HELPER.read_bytes()
        self.active=True; self.socket=True; self.calls=[]; self.inventory={}; self.busy=False
        self.fail_restart=False; self.fail_after_launch=False; self.time=10000.; self.auto_finish=False
        self.cleanup_fails=False; self.claim_clears=False
        a.time=types.SimpleNamespace(time=lambda:self.time,monotonic=lambda:self.time,sleep=self.sleep)
        def cmd(args):
            self.calls.append(args)
            if args[:2]==['systemctl','show']:
                prop=args[args.index('-p')+1]
                return {'DropInPaths':str(a.DROPIN)+(' '+str(a.PAUSE) if a.PAUSE.exists() else ''),
                    'FragmentPath':str(a.UNIT),'User':'ci-runner-manager','Group':'ci-runner-manager','Type':'simple',
                    'KillMode':'control-group','Restart':'no' if a.PAUSE.exists() else 'always',
                    'ExecStart':'{ argv[]='+('/usr/bin/python3 -I '+str(a.SELF)+' drain-worker' if a.PAUSE.exists() else
                        '/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30')+' ; }',
                    'ActiveState':'active' if self.active else 'inactive','ExecMainStatus':'0','NRestarts':'0'}[prop]
            if args[:2]==['systemctl','stop']:
                if args[2]==a.SERVICE:self.active=False
                else:self.socket=False
                return ''
            if args[:2]==['systemctl','start']:
                if args[2]!=a.SERVICE:self.socket=True;return ''
                if a.PAUSE.exists():
                    self.assertTrue(all(v=='shut off' for v in self.inventory.values()))
                    if not self.cleanup_fails:
                        self.inventory={}
                        for path in (*a.STATE.glob('lease-*'),*a.STATE.glob('cleanup-*'),*a.OVERLAY.iterdir()):path.unlink()
                    self.active=False;return ''
                if self.fail_restart:raise a.Refused('command-failed')
                self.active=True
                if self.fail_after_launch:
                    self.running();self.active=False;raise a.Refused('command-failed')
                return ''
            if args[:2] in (['systemctl','daemon-reload'],['systemctl','reset-failed']):return ''
            if args[:2]==['systemctl','is-active']:return 'active' if self.socket else 'inactive'
            if args[:2]==['systemctl','list-units']:return 'busy' if self.busy else ''
            if args[0]=='virsh':
                if 'domstate' in args:return self.inventory[args[-1]]
                return '\n'.join(self.inventory)
            raise AssertionError(args)
        a.command=cmd
        original_module_at=a.module_at
        def module_at(path,expected):
            module=original_module_at(path,expected);module.RUNNER_OVERLAY_ROOT=str(a.OVERLAY);return module
        a.module_at=module_at
        self.output=io.StringIO(); self.capture=contextlib.redirect_stdout(self.output); self.capture.__enter__();self.addCleanup(self.capture.__exit__,None,None,None)
        self.snapshot('before')
    def sleep(self,seconds):
        self.time+=seconds
        if self.auto_finish:self.inventory={n:'shut off' for n in self.inventory}
        if self.claim_clears:(self.a.STATE/'dispatch-history.json').write_text('{}')
    def snapshot(self,which,used=None,active=None):
        a=self.a
        e={'schema':2,'started':self.time,'finished':self.time,'group':{'id':4,'name':'sanctuary-trusted-verification','visibility':'selected',
           'allows_public_repositories':False,'default':False,'inherited':False,'restricted_to_workflows':True,'workflow_restrictions_read_only':False,
           'selected_workflows':a.BEFORE if which=='before' else a.AFTER},'repositories':a.IDS,
           'branches':{repo:[{'label':'default','sha':'1'*40,'refs':used or [],'role_refs':[]}] for repo in a.IDS},'active':active or []}
        (a.STAGE/(which+'.json')).write_bytes(a.encoded(e));return e
    def running(self):
        self.inventory={'sanctuary-ci-gh-1':'running'}
        (self.a.STATE/'lease-gh-1.json').write_text(json.dumps({'lease':'gh-1','phase':'running','launched_at':int(self.time)}))
        (self.a.OVERLAY/'gh-1.qcow2').write_bytes(b'synthetic')
    def drain(self):self.a.drain();self.snapshot('before');self.a.authorize();self.snapshot('after')
    def applied(self):self.drain();self.a.commit()
    def test_success_while_busy_then_rollback_exact_bytes_modes_and_pins(self):
        a=self.a;self.running();self.auto_finish=True;self.applied()
        self.assertTrue(self.active);self.assertFalse(a.PAUSE.exists());self.assertEqual(a.CONFIG.stat().st_mode&0o777,0o640)
        cfg=tomllib.loads(a.CONFIG.read_text());self.assertEqual(cfg['org_workflow_refs'],a.AFTER[:-1]);self.assertEqual(len(cfg['org_workflow_refs']),5)
        self.assertEqual(cfg['org_workflow_refs'][0],a.TRUSTED+a.PINS[-1]);self.assertEqual(cfg['org_workflow_refs'][3],a.TRUSTED+a.PINS[3])
        self.assertEqual(a.sha(a.MANAGER.read_bytes()),a.NEW_MANAGER_SHA);self.assertEqual(a.sha(a.HELPER.read_bytes()),a.NEW_HELPER_SHA)
        expected=copy.deepcopy(self.cfg);expected['org_workflow_refs']=a.AFTER[:-1]
        expected['org_execution_roles']['metadata-preflight']['workflow_ref']=a.NEW
        expected['org_execution_roles']['pr-security-producer']['workflow_ref']=a.AFTER[-1]
        self.assertEqual(cfg,expected)
        self.snapshot('after');a.drain(True);self.snapshot('before');a.recover()
        self.assertEqual(a.CONFIG.read_bytes(),self.original);self.assertEqual(a.MANAGER.read_bytes(),self.binary)
        self.assertEqual(a.HELPER.read_bytes(),self.helper_binary);self.assertTrue(self.active)
    def test_timeout_keeps_jobs_alive_and_old_files_then_abort(self):
        a=self.a;a.DRAIN_TIMEOUT_SECONDS=20;self.running()
        with self.assertRaisesRegex(a.Refused,'drain-timeout'):a.drain()
        self.assertEqual(self.inventory,{'sanctuary-ci-gh-1':'running'});self.assertEqual(a.MANAGER.read_bytes(),self.binary)
        self.assertEqual(a.CONFIG.read_bytes(),self.original);self.assertTrue(a.PAUSE.exists());self.assertFalse(self.active)
        self.snapshot('before',used=[a.OLD]);a.abort();self.assertTrue(self.active)
        self.assertEqual(self.inventory,{'sanctuary-ci-gh-1':'running'})
        self.assertFalse(any('destroy' in call or 'kill' in call for call in self.calls))
    def test_inflight_jit_claim_prevents_stop_until_normal_launch_completed(self):
        a=self.a;(a.STATE/'dispatch-history.json').write_text(json.dumps({'Tuinstra-DEV/gate:1':{'blocked_until':(1<<63)-1,'attempts':1}}))
        self.claim_clears=True;self.drain();self.assertGreater(self.time,10000)
    def test_unresolved_quarantine_times_out_without_service_mutation(self):
        a=self.a;a.ADMISSION_TIMEOUT_SECONDS=2
        (a.STATE/'dispatch-history.json').write_text(json.dumps({'Tuinstra-DEV/gate:1':{'blocked_until':(1<<63)-1,'attempts':1}}))
        with self.assertRaisesRegex(a.Refused,'admission-timeout'):a.drain()
        self.assertTrue(self.active);self.assertFalse(a.PAUSE.exists());self.assertNotIn(['systemctl','stop',a.SERVICE],self.calls)
    def test_busy_helper_does_not_get_stopped(self):
        a=self.a;a.ADMISSION_TIMEOUT_SECONDS=2;self.busy=True
        with self.assertRaisesRegex(a.Refused,'admission-timeout'):a.drain()
        self.assertTrue(self.active);self.assertFalse(a.PAUSE.exists())
    def test_restart_failure_restores_all_old_files_and_waits_for_github_rollback(self):
        a=self.a;self.drain();self.fail_restart=True
        with self.assertRaisesRegex(a.Refused,'command-failed'):a.commit()
        self.assertFalse(self.active);self.assertTrue(a.PAUSE.exists());self.assertEqual(a.CONFIG.read_bytes(),self.original)
        self.assertEqual(a.MANAGER.read_bytes(),self.binary);self.assertEqual(a.HELPER.read_bytes(),self.helper_binary)
        self.assertEqual(a.backup()['phase'],'auto-restored');self.assertIn('AUTO-RESTORED',self.output.getvalue())
        self.fail_restart=False;self.snapshot('before');a.recover();self.assertTrue(self.active)
    def test_restart_failure_after_pickup_retains_new_files_until_guest_finishes(self):
        a=self.a;self.drain();self.fail_after_launch=True
        with self.assertRaises(a.Refused):a.commit()
        self.assertEqual(a.backup()['phase'],'recovery-drain-required');self.assertTrue(a.PAUSE.exists())
        self.assertEqual(a.sha(a.MANAGER.read_bytes()),a.NEW_MANAGER_SHA);self.assertEqual(self.inventory,{'sanctuary-ci-gh-1':'running'})
    def test_retired_ref_in_branch_or_waiting_source_refuses_before_pause(self):
        a=self.a
        for ref in a.FORWARD_REMOVING:
            self.snapshot('before',used=[ref])
            with self.assertRaisesRegex(a.Refused,'ref-in-use'):a.drain()
            self.assertTrue(self.active);self.assertFalse(a.BACKUP.exists())
        self.snapshot('before',active=[{'repo':'Tuinstra-DEV/notify','id':1,'status':'waiting','refs':[a.OLD],'role_refs':[]}])
        with self.assertRaisesRegex(a.Refused,'ref-in-active-run'):a.drain()
    def test_old_metadata_caller_blocks_even_though_ea_trusted_is_preserved(self):
        a=self.a;e=self.snapshot('before');e['branches']['Tuinstra-DEV/tracker'][0]['refs']=[a.TRUSTED+a.PINS[-1]];e['branches']['Tuinstra-DEV/tracker'][0]['role_refs']=[a.TRUSTED+a.PINS[-1]]
        (a.STAGE/'before.json').write_bytes(a.encoded(e))
        with self.assertRaisesRegex(a.Refused,'old-metadata-consumer'):a.drain()
        self.assertFalse(a.PAUSE.exists())
    def test_evidence_age_is_from_first_request_including_collection(self):
        a=self.a;e=self.snapshot('before');e.update(started=self.time-301,finished=self.time-121)
        with self.assertRaisesRegex(a.Refused,'stale-evidence'):a.evidence(a.encoded(e),a.BEFORE,a.FORWARD_REMOVING)
    def test_expired_predrain_evidence_cannot_authorize_patch(self):
        a=self.a;a.drain();self.time+=301
        with self.assertRaisesRegex(a.Refused,'stale-evidence'):a.authorize()
        self.assertEqual(a.backup()['phase'],'drained');self.assertFalse(self.active)
    def test_group_changes_and_seventh_ref_are_rejected(self):
        a=self.a
        for key,val in [('allows_public_repositories',True),('restricted_to_workflows',False),('selected_workflows',a.BEFORE+[a.NEW])]:
            e=self.snapshot('before');e['group'][key]=val
            with self.assertRaises(a.Refused):a.evidence(a.encoded(e),a.BEFORE,())
    def test_wrong_actual_order_or_role_is_rejected(self):
        a=self.a;cfg=copy.deepcopy(self.cfg);cfg['org_workflow_refs'][0:2]=reversed(cfg['org_workflow_refs'][0:2]);a.CONFIG.write_bytes(serialize(cfg))
        with self.assertRaisesRegex(a.Refused,'pin-order-drift'):a.drain()
        self.assertFalse(a.BACKUP.exists())
    def test_wrong_source_or_tampered_backup_refuses(self):
        a=self.a;(a.STAGE/'manager.new').write_bytes(b'bad')
        with self.assertRaisesRegex(a.Refused,'source-hash-drift'):a.drain()
        (a.STAGE/'manager.new').write_bytes((ROOT/'runner/manager/ci_runner_manager.py').read_bytes());self.drain()
        (a.BACKUP/'helper.before').write_bytes(b'bad')
        with self.assertRaisesRegex(a.Refused,'backup-drift'):a.commit()
    def test_prior_completed_transaction_is_preserved_not_reused(self):
        a=self.a;a.OLD_BACKUP.mkdir(mode=0o700);old=a.encoded({'phase':'applied'});(a.OLD_BACKUP/'journal.json').write_bytes(old)
        self.applied();self.assertEqual((a.OLD_BACKUP/'journal.json').read_bytes(),old)
    def test_prior_pause_or_unfinished_transaction_refuses(self):
        a=self.a;a.OLD_BACKUP.mkdir(mode=0o700);(a.OLD_BACKUP/'journal.json').write_bytes(a.encoded({'phase':'paused'}))
        with self.assertRaisesRegex(a.Refused,'previous-transaction-unfinished'):a.drain()
        (a.OLD_BACKUP/'journal.json').write_bytes(a.encoded({'phase':'applied'}));a.OLD_PAUSE.write_bytes(b'old')
        with self.assertRaisesRegex(a.Refused,'previous-pause-present'):a.drain()
    def test_postpause_config_drift_refuses_commit(self):
        a=self.a;self.drain();a.CONFIG.write_bytes(self.original+b'# unexpected\n')
        with self.assertRaisesRegex(a.Refused,'config-drift'):a.commit()
        self.assertFalse(self.active)
    def test_rollback_refuses_new_consumers_before_pausing(self):
        a=self.a;self.applied();self.snapshot('after',used=[a.NEW])
        with self.assertRaisesRegex(a.Refused,'ref-in-use'):a.drain(True)
        self.assertTrue(self.active);self.assertFalse(a.PAUSE.exists())
    def test_cleanup_failure_is_bounded_without_force(self):
        a=self.a;a.DRAIN_TIMEOUT_SECONDS=20;self.running();self.auto_finish=True;self.cleanup_fails=True
        with self.assertRaisesRegex(a.Refused,'drain-timeout'):a.drain()
        self.assertEqual(a.CONFIG.read_bytes(),self.original);self.assertTrue(a.PAUSE.exists())
    def test_exact_hosted_metadata_optout_preserves_ea_trusted_consumer(self):
        a=self.a;ref=a.TRUSTED+a.PINS[-1]
        for inputs,expected in [('',[ref]),('    with: {request-isolated-role-runner: false}\n',[]),('    with: {request-isolated-role-runner: "false"}\n',[ref])]:
            text='name: fixture\non: pull_request\njobs:\n  quality:\n    uses: '+ref+'\n'+inputs
            self.assertEqual(a.isolated_metadata_refs('Tuinstra-DEV/tracker',text),expected)
        # A false input in an unrelated job does not disable this role caller.
        text='jobs:\n  isolated: {uses: '+ref+'}\n  hosted: {uses: '+ref+', with: {request-isolated-role-runner: false}}'
        self.assertEqual(a.isolated_metadata_refs('Tuinstra-DEV/tracker',text),[ref])
        e=self.snapshot('before');e['branches']['Tuinstra-DEV/tracker'][0]['refs']=[ref]
        (a.STAGE/'before.json').write_bytes(a.encoded(e));self.drain()
    def test_collector_requires_extra_resume_budget(self):
        a=self.a;self.drain();e=self.snapshot('after');e.update(started=self.time-256,finished=self.time-76)
        (a.STAGE/'after.json').write_bytes(a.encoded(e))
        # Authorize timestamp binding also prevents reusing pre-pause evidence.
        doc=a.backup();doc['authorized_at']=self.time-300;a.journal(doc)
        with self.assertRaisesRegex(a.Refused,'insufficient-evidence'):a.commit()
        self.assertFalse(self.active)
    def test_continue_after_timeout_drains_without_reusing_old_evidence(self):
        a=self.a;a.DRAIN_TIMEOUT_SECONDS=20;self.running()
        with self.assertRaisesRegex(a.Refused,'drain-timeout'):a.drain()
        self.auto_finish=True;a.continue_drain();self.snapshot('before');a.authorize()
        self.assertEqual(a.backup()['phase'],'authorized')
    def test_retry_admission_uses_existing_backup_after_quarantine_resolves(self):
        a=self.a;a.ADMISSION_TIMEOUT_SECONDS=1
        history=a.STATE/'dispatch-history.json';history.write_text(json.dumps({'Tuinstra-DEV/gate:1':{'blocked_until':(1<<63)-1,'attempts':1}}))
        with self.assertRaisesRegex(a.Refused,'admission-timeout'):a.drain()
        history.write_text('{}');self.snapshot('before');a.continue_drain()
        self.assertEqual(a.backup()['phase'],'drained')
    def test_rollback_timeout_can_abort_to_after_without_touching_guest(self):
        a=self.a;self.applied();self.running();a.DRAIN_TIMEOUT_SECONDS=20;self.snapshot('after')
        with self.assertRaisesRegex(a.Refused,'drain-timeout'):a.drain(True)
        self.snapshot('after');a.abort_rollback();self.assertTrue(self.active)
        self.assertEqual(self.inventory,{'sanctuary-ci-gh-1':'running'});self.assertEqual(a.backup()['phase'],'applied')
    def test_cleanup_guard_rejects_live_paused_and_unknown_domains_before_destroy(self):
        a=self.a
        for state in ('running','paused','blocked','pmsuspended','unknown'):
            calls=[]
            def helper(cfg,operation,*args,**kwargs):
                calls.append(operation)
                return types.SimpleNamespace(stdout=a.encoded({'gh-1':state}))
            mod=types.SimpleNamespace(helper=helper,validate_inventory=lambda d:d)
            a.install_cleanup_guard(mod)
            with self.assertRaisesRegex(a.Refused,'worker-live-domain-refused'):mod.helper({},'destroy','gh-1')
            self.assertEqual(calls,['list'])
        calls=[]
        def helper(cfg,operation,*args,**kwargs):
            calls.append(operation);return types.SimpleNamespace(stdout=a.encoded({'gh-1':'shut off'}))
        mod=types.SimpleNamespace(helper=helper,validate_inventory=lambda d:d);a.install_cleanup_guard(mod)
        mod.helper({},'destroy','gh-1');self.assertEqual(calls,['list','destroy'])
        with self.assertRaisesRegex(a.Refused,'worker-admission-denied'):mod.helper({},'launch','gh-1')
    def test_worker_refuses_root_execution(self):
        with patch.object(self.a.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(self.a.Refused,'worker-must-use-existing-service-user'):self.a.worker()
    def test_readback_checks_exact_group_controls_and_repository_ids_without_dump(self):
        a=self.a;e=self.snapshot('before')
        def run(args,**kwargs):
            doc=e['group'] if args[-1].endswith('/4') else {'total_count':len(a.IDS),'repositories':[
                {'full_name':repo,'id':rid,'private':True} for repo,rid in a.IDS.items()]}
            return types.SimpleNamespace(returncode=0,stdout=json.dumps(doc))
        with patch.object(a.os,'geteuid',return_value=501),patch.object(a.subprocess,'run',side_effect=run):
            a.readback(False)
            self.assertIn('GROUP-BEFORE-MATCH',self.output.getvalue())
            e['group']['allows_public_repositories']=True
            with self.assertRaisesRegex(a.Refused,'group-controls-differ'):a.readback(False)
    def test_empty_repository_selection_is_rejected_on_readback(self):
        a=self.a;e=self.snapshot('before')
        def run(args,**kwargs):
            return types.SimpleNamespace(returncode=0,stdout=json.dumps(e['group'] if args[-1].endswith('/4') else {'total_count':0,'repositories':[]}))
        with patch.object(a.os,'geteuid',return_value=501),patch.object(a.subprocess,'run',side_effect=run):
            with self.assertRaisesRegex(a.Refused,'readback-repository-count-drift'):a.readback(False)
    def test_subprocesses_share_remaining_deadline_instead_of_resetting_budget(self):
        b=load();b.time=self.a.time;called=[]
        def run(args,**kwargs):
            called.append(kwargs['timeout']);return types.SimpleNamespace(returncode=0,stdout='safe')
        with patch.object(b.subprocess,'run',side_effect=run):
            with b.bounded_commands(2):
                b.command(['fixed-read']);self.time+=1.5;b.command(['fixed-read']);self.time+=1
                with self.assertRaisesRegex(b.Refused,'bounded-wait-expired'):b.command(['fixed-read'])
        self.assertEqual(called,[2,.5]);self.assertIsNone(b.COMMAND_DEADLINE)
    def test_no_config_or_secret_contents_in_success_or_failure_output(self):
        self.applied();out=self.output.getvalue()
        self.assertNotIn('github_token_file',out);self.assertNotIn(self.original.decode(),out)

if __name__=='__main__':unittest.main()
