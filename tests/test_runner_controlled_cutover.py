"""Offline transaction tests; never run sudo, gh, virsh, systemctl or a network."""
import contextlib
import builtins
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time as real_time
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
        for name in ('MANAGER','CONFIG','UNIT','HELPER','DROPIN','PAUSE','STATE','OVERLAY','STAGE','BACKUP','OLD_BACKUP','OLD_PAUSE','LOCK','TRANSACTIONS','GATE','READY','BOOT_FILE','SELF','WRAPPER'):
            setattr(a,name,self.root/name.lower())
        a.JOURNAL=a.BACKUP/'journal.json';a.ACTIVE=a.TRANSACTIONS/'active.json'
        a.BOOT_FILE.write_text('11111111-1111-4111-8111-111111111111')
        a.SELF.write_bytes(SCRIPT.read_bytes())
        a.BLOCK=a.BLOCK.replace(b'/usr/local/sbin/runner-controlled-cutover.py',str(a.SELF).encode())
        a.RUNBLOCK=a.RUNBLOCK.replace(b'/usr/local/libexec/runner-cutover-admission.py',str(a.WRAPPER).encode())
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
        self.configure_runtime()
        self.output=io.StringIO(); self.capture=contextlib.redirect_stdout(self.output); self.capture.__enter__();self.addCleanup(self.capture.__exit__,None,None,None)
        self.snapshot('before')
    def configure_runtime(self):
        a=self.a
        self.active=True; self.socket=True; self.calls=[]; self.inventory={}; self.busy=False
        self.fail_restart=False; self.fail_after_launch=False; self.time=10000.; self.auto_finish=False
        self.cleanup_fails=False; self.claim_clears=False
        a.time=types.SimpleNamespace(time=lambda:self.time,monotonic=lambda:self.time,sleep=self.sleep)
        # Model filesystem CLOCK_REALTIME in the same virtual clock as the controller.
        # Store the publication timestamp in mtime so it survives a fixture child exit;
        # production uses the kernel's unforgeable-by-utime st_ctime_ns instead.
        original_chmod=Path.chmod;original_read=a.read
        def chmod(path,mode,*args,**kwargs):
            result=original_chmod(path,mode,*args,**kwargs)
            if path==a.GATE:
                stamp=int(self.time*1_000_000_000);os.utime(path,ns=(stamp,stamp))
            return result
        def read(path):
            data,info=original_read(path)
            if path==a.GATE:
                values={key:getattr(info,key) for key in dir(info) if key.startswith('st_')}
                values['st_ctime_ns']=info.st_mtime_ns;info=types.SimpleNamespace(**values)
            return data,info
        a.read=read;self.real_read=original_read
        chmod_patch=patch.object(Path,'chmod',chmod);chmod_patch.start();self.addCleanup(chmod_patch.stop)
        def cmd(args):
            self.calls.append(args)
            if args[:2]==['systemctl','show']:
                prop=args[args.index('-p')+1]
                return {'DropInPaths':str(a.DROPIN)+(' '+str(a.PAUSE) if a.PAUSE.exists() else ''),
                    'FragmentPath':str(a.UNIT),'User':'ci-runner-manager','Group':'ci-runner-manager','Type':'simple',
                    'KillMode':'control-group','Restart':'no' if a.PAUSE.exists() and a.PAUSE.read_bytes()==a.BLOCK else 'always',
                    'ExecStart':'{ argv[]='+('/usr/bin/python3 -I '+str(a.SELF if a.PAUSE.read_bytes()==a.BLOCK else a.WRAPPER)+(' drain-worker' if a.PAUSE.read_bytes()==a.BLOCK else ' admission-worker') if a.PAUSE.exists() else
                        '/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30')+' ; }',
                    'ActiveState':'active' if self.active else 'inactive','ExecMainStatus':'0','NRestarts':'0','MainPID':'123' if self.active else '0','ExecMainStartTimestampMonotonic':'100'}[prop]
            if args[:2]==['systemctl','stop']:
                if args[2]==a.SERVICE:self.active=False
                else:self.socket=False
                return ''
            if args[:2]==['systemctl','start']:
                if args[2]!=a.SERVICE:self.socket=True;return ''
                if a.PAUSE.exists() and a.PAUSE.read_bytes()==a.BLOCK:
                    self.assertTrue(all(v=='shut off' for v in self.inventory.values()))
                    if not self.cleanup_fails:
                        self.inventory={}
                        for path in (*a.STATE.glob('lease-*'),*a.STATE.glob('cleanup-*'),*a.OVERLAY.iterdir()):path.unlink()
                    self.active=False;return ''
                if self.fail_restart:raise a.Refused('command-failed')
                self.active=True
                if a.PAUSE.exists() and a.PAUSE.read_bytes()==a.RUNBLOCK:
                    ticket=a.check_admission_ticket(a.STABILITY_SECONDS);a.write_ready(ticket)
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

    def attach_existing(self,root,state):
        self.root=Path(root);self.a=load();a=self.a;a.UID=os.getuid();a.ANCHOR=self.root
        for name in ('MANAGER','CONFIG','UNIT','HELPER','DROPIN','PAUSE','STATE','OVERLAY','STAGE','BACKUP','OLD_BACKUP','OLD_PAUSE','LOCK','TRANSACTIONS','GATE','READY','BOOT_FILE','SELF','WRAPPER'):
            setattr(a,name,self.root/name.lower())
        a.JOURNAL=a.BACKUP/'journal.json';a.ACTIVE=a.TRANSACTIONS/'active.json'
        a.BLOCK=a.BLOCK.replace(b'/usr/local/sbin/runner-controlled-cutover.py',str(a.SELF).encode())
        a.RUNBLOCK=a.RUNBLOCK.replace(b'/usr/local/libexec/runner-cutover-admission.py',str(a.WRAPPER).encode())
        self.configure_runtime()
        for key,value in state.items():setattr(self,key,value)
    def crash_process(self,action,point='before-start'):
        # Fresh interpreter: direct fork + Python work is unsafe on instrumented macOS.
        # os._exit in that interpreter still skips every handler/finally in the controller.
        state={key:getattr(self,key) for key in ('time','active','socket','inventory')}
        proc=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--crash-fixture',str(self.root),json.dumps(state),action,point],capture_output=True,text=True,timeout=30)
        self.assertEqual(proc.returncode,73,msg='crash fixture did not reach the requested operation: '+proc.stderr)

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
        self.assertTrue(self.active);self.assertEqual(a.PAUSE.read_bytes(),a.RUNBLOCK);self.assertEqual(a.CONFIG.stat().st_mode&0o777,0o640)
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
        self.assertEqual(a.backup()['phase'],'recovery-draining');self.assertTrue(a.PAUSE.exists())
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
        self.assertTrue(self.active);self.assertEqual(a.PAUSE.read_bytes(),a.RUNBLOCK)
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
    def test_review_r1_crash_at_resume_is_boot_closed_and_recoverable(self):
        a=self.a;self.drain();self.crash_process('commit')
        doc=a.backup();self.assertEqual(doc['phase'],'resuming')
        self.active=False
        with patch.object(a,'boot_identity',return_value='another-boot'):
            with self.assertRaisesRegex(a.Refused,'boot'):a.check_admission_ticket()
        a.reconcile_recovery();self.assertEqual(a.backup()['phase'],'recovery-drained')
        self.snapshot('before');a.recover();a.recover()
        self.assertTrue(self.active);self.assertEqual(a.CONFIG.read_bytes(),self.original)
    def test_review_r2a_quarantine_failure_records_intent_before_safe_stop_refusal(self):
        a=self.a;self.drain();original_command=a.command;history=a.STATE/'dispatch-history.json'
        claim=json.dumps({'Tuinstra-DEV/gate:1':{'blocked_until':(1<<63)-1,'attempts':1}})
        def fail(args):
            result=original_command(args)
            if args==['systemctl','start',a.SERVICE] and (not a.PAUSE.exists() or a.PAUSE.read_bytes()!=a.BLOCK):
                self.active=True;history.write_text(claim);raise a.Refused('injected-start-failure')
            return result
        a.command=fail;a.ADMISSION_TIMEOUT_SECONDS=2
        with self.assertRaises(a.Refused):a.commit()
        self.assertTrue(a.backup()['phase'].startswith('recovery-'))
        self.assertEqual(history.read_text(),claim)
        self.assertEqual(a.decode(a.read(a.GATE)[0])['phase'],'blocked')
        a.command=original_command;history.write_text('{}') # External fixture: original JIT settled normally.
        a.reconcile_recovery();self.assertEqual(a.backup()['phase'],'recovery-drained')
        self.snapshot('before');a.recover();self.assertTrue(self.active)
    def test_review_r2b_old_manager_pickup_failure_has_recovery_drain(self):
        a=self.a;self.drain();self.fail_restart=True
        with self.assertRaises(a.Refused):a.commit()
        self.fail_restart=False;self.fail_after_launch=True;self.snapshot('before')
        with self.assertRaises(a.Refused):a.recover()
        self.assertEqual(a.backup()['phase'],'recovery-draining')
        self.assertEqual(a.backup()['file_set'],'before')
        self.assertEqual(self.inventory,{'sanctuary-ci-gh-1':'running'})
        self.fail_after_launch=False;self.auto_finish=True
        a.reconcile_recovery();self.snapshot('before');a.recover()
        self.assertTrue(self.active);self.assertEqual(a.CONFIG.read_bytes(),self.original)
    def test_review_r3_slow_helper_cannot_open_admission_at_304_seconds(self):
        a=self.a;self.drain();proof=self.snapshot('after');proof.update(started=self.time-250,finished=self.time-70)
        (a.STAGE/'after.json').write_bytes(a.encoded(proof));doc=a.backup();doc['authorized_at']=self.time-300;a.journal(doc)
        original=a.command;opened=[]
        def slow(args):
            if args==['systemctl','start','ci-runner-host-helper.socket']:self.time+=54
            if args==['systemctl','start',a.SERVICE] and (not a.PAUSE.exists() or a.PAUSE.read_bytes()!=a.BLOCK):opened.append(self.time)
            return original(args)
        a.command=slow
        try:a.commit()
        except a.Refused:pass
        self.assertEqual(opened,[])
        self.assertNotEqual(a.backup()['phase'],'applied')
        self.assertNotIn('APPLIED;',self.output.getvalue())
    def test_review_r4a_abort_can_retry_and_preserves_verified_original_backup(self):
        a=self.a;a.drain();self.snapshot('before');a.abort();previous=a.BACKUP
        original_backup=(previous/'config.before').read_bytes()
        self.snapshot('before');a.drain()
        self.assertNotEqual(a.BACKUP,previous)
        self.assertEqual((previous/'config.before').read_bytes(),original_backup)
        self.assertEqual(a.backup()['phase'],'drained')
    def test_review_r4b_crash_before_initial_journal_allows_new_transaction(self):
        a=self.a;original=a.write
        def fail(path,data,info=None):
            if path.name=='journal.json':raise RuntimeError('injected-first-journal-failure')
            return original(path,data,info)
        with patch.object(a,'write',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'injected-first-journal'):a.drain()
        previous=a.BACKUP
        a.drain();self.assertNotEqual(a.BACKUP,previous);self.assertTrue(previous.is_dir())
        self.assertEqual(a.backup()['phase'],'drained')
    def test_review_r4_completed_backup_before_drain_intent_can_retry(self):
        a=self.a;original=a.journal
        def fail(doc):
            original(doc)
            if doc['phase']=='prepared':raise RuntimeError('injected-after-backup')
        with patch.object(a,'journal',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'injected-after-backup'):a.drain()
        previous=a.BACKUP;original_bytes=(previous/'manager.before').read_bytes()
        a.drain();self.assertEqual(a.BACKUP,previous)
        self.assertEqual((previous/'manager.before').read_bytes(),original_bytes)
        self.assertEqual(a.backup()['phase'],'drained')
    def test_review_r4c_partial_backup_is_verified_and_completed_without_overwrite(self):
        a=self.a;original=a.write
        def fail(path,data,info=None):
            if path.name=='helper.before':raise RuntimeError('injected-partial-backup')
            return original(path,data,info)
        with patch.object(a,'write',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'injected-partial'):a.drain()
        previous=a.BACKUP;original_bytes=(previous/'manager.before').read_bytes()
        a.drain();self.assertEqual(a.BACKUP,previous)
        self.assertEqual((previous/'manager.before').read_bytes(),original_bytes)
        self.assertEqual(a.backup()['phase'],'drained')
    def test_review_r1_old_recover_crash_is_boot_closed(self):
        a=self.a;self.drain();self.fail_restart=True
        with self.assertRaises(a.Refused):a.commit()
        self.fail_restart=False;self.snapshot('before');self.crash_process('recover')
        self.assertEqual(a.backup()['phase'],'resuming');self.assertEqual(a.backup()['file_set'],'before')
        with patch.object(a,'boot_identity',return_value='new-boot'):
            with self.assertRaisesRegex(a.Refused,'boot'):a.check_admission_ticket()
        self.active=False;a.reconcile_recovery();self.snapshot('before');a.recover();a.recover()
        self.assertTrue(self.active)
    def test_review_r1_crash_between_terminal_journal_and_enable_is_boot_closed(self):
        a=self.a;self.drain();self.crash_process('commit','before-enabled')
        self.assertEqual(a.backup()['phase'],'applied');self.active=False
        with patch.object(a,'boot_identity',return_value='new-boot'):
            with self.assertRaisesRegex(a.Refused,'boot'):a.check_admission_ticket()
        a.reconcile_recovery();self.snapshot('before');a.recover();self.assertTrue(self.active)
    def test_review_r1_missing_override_repair_preserves_running_guest(self):
        a=self.a;self.applied();doc=a.backup();doc['phase']='resuming';a.journal(doc)
        a.PAUSE.unlink();self.running();self.auto_finish=True
        a.reconcile_recovery();self.assertEqual(a.PAUSE.read_bytes(),a.BLOCK)
        self.assertEqual(a.backup()['phase'],'recovery-drained')
        self.assertFalse(any('destroy' in call or 'kill' in call for call in self.calls))
    def test_review_r2_inactive_quarantine_is_preserved_and_recovery_intent_survives(self):
        a=self.a;self.drain();original=a.command;history=a.STATE/'dispatch-history.json'
        claim=json.dumps({'Tuinstra-DEV/gate:1':{'blocked_until':(1<<63)-1,'attempts':1}})
        def fail(args):
            result=original(args)
            if args==['systemctl','start',a.SERVICE] and a.PAUSE.read_bytes()==a.RUNBLOCK:
                history.write_text(claim);self.active=False;raise a.Refused('injected-inactive-quarantine')
            return result
        a.command=fail
        with self.assertRaises(a.Refused):a.commit()
        self.assertEqual(a.backup()['phase'],'recovery-stopping');self.assertEqual(history.read_text(),claim)
        self.assertEqual(a.decode(a.read(a.GATE)[0])['phase'],'blocked')
    def test_review_r3_stability_overrun_cannot_report_applied(self):
        a=self.a;self.drain();proof=self.snapshot('after');proof.update(started=self.time-250,finished=self.time-70)
        (a.STAGE/'after.json').write_bytes(a.encoded(proof));doc=a.backup();doc['authorized_at']=self.time-300;a.journal(doc)
        original=a.time.sleep
        def slow(seconds):original(seconds+20 if seconds==a.STABILITY_SECONDS else seconds)
        a.time.sleep=slow
        with self.assertRaisesRegex(a.Refused,'insufficient-evidence'):a.commit()
        self.assertNotEqual(a.backup()['phase'],'applied');self.assertNotIn('APPLIED;',self.output.getvalue())
    def test_review_r3_terminal_journal_delay_cannot_enable_expired_admission(self):
        a=self.a;self.drain();original=a.journal
        def slow(doc):
            original(doc)
            if doc['phase']=='applied':self.time+=301
        with patch.object(a,'journal',side_effect=slow):
            with self.assertRaisesRegex(a.Refused,'insufficient-evidence'):a.commit()
        self.assertEqual(a.decode(a.read(a.GATE)[0])['phase'],'blocked')
        self.assertNotIn('APPLIED;',self.output.getvalue())
        a.reconcile_recovery();self.assertEqual(a.backup()['phase'],'recovery-drained')
    def test_review_r3_live_get_delay_rechecks_ticket_before_any_admission(self):
        a=self.a;self.applied();record=a.decode(a.read(a.GATE)[0]);record.update(phase='probation',expires=self.time+70,monotonic_expires=self.time+70,boot=a.boot_identity())
        a.write(a.GATE,a.encoded(record));a.GATE.chmod(0o644)
        def slow(*args):self.time+=36
        with patch.object(a.os,'geteuid',return_value=501),patch.object(a,'verify_live_group',side_effect=slow),patch.object(a,'write_ready') as ready:
            with self.assertRaisesRegex(a.Refused,'proof-expired'):a.admission_worker()
            ready.assert_not_called()
    def test_review_r4_partial_backup_tampering_is_not_overwritten_on_retry(self):
        a=self.a;original=a.write
        def fail(path,data,info=None):
            if path.name=='helper.before':raise RuntimeError('injected-partial')
            return original(path,data,info)
        with patch.object(a,'write',side_effect=fail):
            with self.assertRaises(RuntimeError):a.drain()
        target=a.BACKUP/'manager.before';target.write_bytes(b'tampered')
        with self.assertRaisesRegex(a.Refused,'partial-backup-drift'):a.drain()
        self.assertEqual(target.read_bytes(),b'tampered');self.assertTrue(self.active)
    def test_review_runtime_guard_rejects_new_jit_without_clearing_existing_claim(self):
        a=self.a;self.applied();ticket=a.check_admission_ticket();mod=a.installed(True)
        calls=[]
        with patch.object(mod.GitHubClient,'request',side_effect=lambda *args,**kwargs:calls.append(args)):
            a.install_runtime_admission_guard(mod,ticket);client=object.__new__(mod.GitHubClient)
            a.block_admission()
            with self.assertRaises(mod.OrgPreflightRejected):client.request('POST','/orgs/Tuinstra-DEV/actions/runners/generate-jitconfig',{})
            self.assertEqual(calls,[])
    def test_enabled_guard_can_reboot_long_after_activation_proof_expired(self):
        a=self.a;self.applied();ticket=a.check_admission_ticket();self.time+=1000
        calls=[];mod=types.SimpleNamespace()
        mod.open_url=lambda request,timeout=20:calls.append((request.full_url,timeout))
        client=types.SimpleNamespace()
        client.request=lambda method,path:mod.open_url(types.SimpleNamespace(get_method=lambda:method,full_url='https://api.github.com'+path,data=None))
        client.verify_org_group=lambda cfg:[client.request('GET',path) for path in a.LIVE_PATHS]
        mod.organization_client=lambda cfg:client
        a.verify_live_group(mod,self.cfg,ticket)
        self.assertEqual([call[0] for call in calls],['https://api.github.com'+p for p in a.LIVE_PATHS])
        self.assertEqual(a.check_admission_ticket()['phase'],'enabled')
    def test_recover_repairs_old_terminal_journal_with_unfinished_gate_publication(self):
        a=self.a;self.applied();self.snapshot('after');a.drain(True);self.snapshot('before');a.recover()
        doc=a.backup();ticket=a.decode(a.read(a.GATE)[0]);ticket['phase']='probation';a.write(a.GATE,a.encoded(ticket));a.GATE.chmod(0o644)
        a.recover();self.assertEqual(a.backup()['phase'],'recovery-drained')
        self.snapshot('before');a.recover();self.assertTrue(self.active)
    def test_recovery_refuses_changed_service_unit_before_signaling_it(self):
        a=self.a;self.drain();doc=a.backup();doc['phase']='resuming';a.journal(doc)
        a.UNIT.write_bytes(b'changed-unit');before=len(self.calls)
        with self.assertRaisesRegex(a.Refused,'recovery-unit-drift'):a.reconcile_recovery()
        self.assertFalse(any(call[:2]==['systemctl','stop'] for call in self.calls[before:]))
    def test_partial_installation_recovery_checks_all_three_real_file_hashes(self):
        a=self.a;self.drain();doc=a.backup();doc['phase']='installing';a.journal(doc)
        a.write(a.MANAGER,(a.STAGE/'manager.new').read_bytes(),a.MANAGER.stat())
        self.assertEqual(a.observed_files(doc),'mixed')
        a.reconcile_recovery();self.snapshot('before');a.recover()
        self.assertEqual(a.MANAGER.read_bytes(),self.binary);self.assertEqual(a.HELPER.read_bytes(),self.helper_binary)
    def test_admission_worker_cannot_run_as_root(self):
        with patch.object(self.a.os,'geteuid',return_value=0):
            with self.assertRaisesRegex(self.a.Refused,'must-use-existing-service-user'):self.a.admission_worker()
    def test_enabled_guard_detects_changed_installed_operator_package(self):
        a=self.a;self.applied();a.SELF.write_bytes(b'changed-package')
        with self.assertRaisesRegex(a.Refused,'admission-file-drift'):a.check_admission_ticket()
    def test_admission_preflight_creates_lifecycle_lock_before_live_gets(self):
        a=self.a;self.applied();a.LOCK.unlink()
        def probe(*args):
            handle=a.take_lock();self.assertIsNotNone(handle);os.close(handle)
            raise a.Refused('injected-live-get-refusal')
        with patch.object(a.os,'geteuid',return_value=501),patch.object(a,'verify_live_group',side_effect=probe):
            with self.assertRaisesRegex(a.Refused,'injected-live-get-refusal'):a.admission_worker()
    def test_no_config_or_secret_contents_in_success_or_failure_output(self):
        self.applied();out=self.output.getvalue()
        self.assertNotIn('github_token_file',out);self.assertNotIn(self.original.decode(),out)
    def test_last_enabled_write_checks_deadline_after_temporary_fsync(self):
        a=self.a;self.drain();original=a.os.fsync;injected=[]
        original_write=a.write
        def write(path,data,*args,**kwargs):
            if path==a.GATE and a.decode(data)['phase']=='enabled':
                def delayed(fd):
                    original(fd)
                    if not injected:self.time+=301;injected.append(True)
                with patch.object(a.os,'fsync',side_effect=delayed):return original_write(path,data,*args,**kwargs)
            return original_write(path,data,*args,**kwargs)
        with patch.object(a,'write',side_effect=write):
            with self.assertRaisesRegex(a.Refused,'expired'):a.commit()
        self.assertTrue(injected);self.assertNotEqual(a.backup()['phase'],'applied')
        self.assertEqual(a.decode(a.read(a.GATE)[0])['phase'],'blocked')
        self.assertNotIn('APPLIED;',self.output.getvalue())
    def test_live_readback_refuses_extra_get_before_network(self):
        a=self.a;mod=types.SimpleNamespace(open_url=lambda *args,**kwargs:None)
        calls=[];client=types.SimpleNamespace(request=lambda *args,**kwargs:calls.append(args))
        client.verify_org_group=lambda cfg:client.request('GET','/user')
        mod.organization_client=lambda cfg:client
        with self.assertRaisesRegex(a.Refused,'live-readback'):
            a.verify_live_group(mod,self.cfg,{'phase':'enabled'})
        self.assertEqual(calls,[])
    def test_finalize_returns_to_original_service_and_preserves_running_job(self):
        a=self.a;self.applied();self.running();before=self.time
        a.finalize()
        self.assertGreaterEqual(self.time-before,a.FINALIZE_STABILITY_SECONDS)
        self.assertFalse(a.PAUSE.exists());self.assertFalse(a.WRAPPER.exists());self.assertFalse(a.GATE.exists())
        self.assertTrue(self.active);self.assertEqual(self.inventory,{'sanctuary-ci-gh-1':'running'})
        self.assertEqual(a.backup()['phase'],'finalized');a.finalize()
        self.assertEqual(a.CONFIG.read_bytes(),(a.BACKUP/'config.after').read_bytes())
    def test_finalize_unstable_manager_keeps_guard_and_files(self):
        a=self.a;self.applied();original=a.time.sleep
        def fail(seconds):original(seconds);self.active=False
        a.time.sleep=fail
        with self.assertRaisesRegex(a.Refused,'terminal-manager-inactive'):a.finalize()
        self.assertTrue(a.PAUSE.exists());self.assertEqual(a.backup()['phase'],'applied')
    def test_wrapper_credential_boundary_uses_only_existing_systemd_file_and_two_gets(self):
        a=self.a;self.applied();token='synthetic-credential-boundary-73f12';credential=Path(a.ORG_CREDENTIAL)
        # Exercise the real, hash-pinned credential/client code in BOTH versions.
        for path,digest in [(a.BACKUP/'manager.before',a.MANAGER_SHA),(a.MANAGER,a.NEW_MANAGER_SHA)]:
            mod=a.module_at(path,digest);reads=[];requests=[];env_reads=[]
            class Environment(dict):
                def get(self,key,default=None):env_reads.append(key);return super().get(key,default)
                def __getitem__(self,key):env_reads.append(key);return super().__getitem__(key)
                def __setitem__(self,key,value):raise AssertionError('wrapper changed environment')
            environment=Environment(CREDENTIALS_DIRECTORY=str(credential.parent),GH_TOKEN='wrong-synthetic-source')
            original_open=io.open;original_stat=Path.stat;original_os_open=os.open;original_os_write=os.write
            class Stream:
                def __init__(self,wrapped):self.wrapped=wrapped
                def __getattr__(self,key):return getattr(self.wrapped,key)
                def __enter__(self):self.wrapped.__enter__();return self
                def __exit__(self,*args):return self.wrapped.__exit__(*args)
                def write(self,data):
                    self_outer.assertNotIn(token.encode() if isinstance(data,bytes) else token,data)
                    return self.wrapped.write(data)
            self_outer=self
            def file_open(file,*args,**kwargs):
                if not isinstance(file,int) and Path(file)==credential:
                    self.assertEqual(args[0] if args else kwargs.get('mode','r'),'r')
                    reads.append(str(file));return io.StringIO(token)
                if not isinstance(file,int):self.assertTrue(Path(file).is_relative_to(self.root),'unexpected file/credential source')
                return Stream(original_open(file,*args,**kwargs))
            def low_open(path,*args,**kwargs):
                self.assertTrue(Path(path).is_relative_to(self.root),'unexpected file/credential source')
                return original_os_open(path,*args,**kwargs)
            def low_write(fd,data):
                self.assertNotIn(token.encode(),data);return original_os_write(fd,data)
            def file_stat(path,*args,**kwargs):
                if path==credential:return types.SimpleNamespace(st_mode=0o100600)
                return original_stat(path,*args,**kwargs)
            def http(request,timeout=20):
                self.assertEqual(request.get_method(),'GET');self.assertIsNone(request.data)
                self.assertEqual(request.full_url,'https://api.github.com'+a.LIVE_PATHS[len(requests)])
                self.assertEqual(request.get_header('Authorization'),'Bearer '+token)
                self.assertNotIn(token,request.full_url)
                requests.append(request.full_url)
                response=self.snapshot('after')['group'] if len(requests)==1 else {'total_count':len(a.IDS),
                    'repositories':[{'full_name':repo,'id':rid,'private':True} for repo,rid in a.IDS.items()]}
                return io.BytesIO(json.dumps(response).encode())
            def daemon(args):
                self.assertEqual(args,['--config',str(a.CONFIG),'daemon','--interval','30']);return 0
            # Substitute only network, synthetic credential I/O and the daemon boundary.
            # Any subprocess, alternate credential read, argv/env export or file export fails.
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(a.os,'geteuid',return_value=os.getuid() or 501))
                stack.enter_context(patch.object(a,'module_at',return_value=mod))
                stack.enter_context(patch.object(a.os,'environ',environment))
                stack.enter_context(patch.object(io,'open',side_effect=file_open))
                stack.enter_context(patch.object(builtins,'open',side_effect=file_open))
                stack.enter_context(patch.object(os,'open',side_effect=low_open))
                stack.enter_context(patch.object(os,'write',side_effect=low_write))
                stack.enter_context(patch.object(Path,'stat',file_stat))
                stack.enter_context(patch.object(mod,'open_url',side_effect=http))
                stack.enter_context(patch.object(mod,'main',side_effect=daemon))
                for name in ('run','Popen','call','check_call','check_output'):
                    stack.enter_context(patch.object(subprocess,name,side_effect=AssertionError('wrapper spawned subprocess')))
                stack.enter_context(patch.object(os,'system',side_effect=AssertionError('wrapper spawned shell')))
                for name in dir(os):
                    if name.startswith(('exec','spawn','posix_spawn')) or name in ('putenv','unsetenv','fork','forkpty'):
                        stack.enter_context(patch.object(os,name,side_effect=AssertionError('wrapper exported credential/process state')))
                a.admission_worker()
            self.assertEqual(reads,[a.ORG_CREDENTIAL]);self.assertEqual(len(requests),2)
            self.assertTrue(set(env_reads)<= {'CREDENTIALS_DIRECTORY'})
            self.assertNotIn(token,repr(sys.argv));self.assertNotIn(token,self.output.getvalue())
            for file in self.root.rglob('*'):
                if file.is_file():self.assertNotIn(token.encode(),file.read_bytes())
    def test_live_readback_rejects_alternative_credential_or_api_before_loading_client(self):
        a=self.a;mod=types.SimpleNamespace(organization_client=lambda cfg:self.fail('credential client must not load'))
        for key,value in [('org_github_token_file','/etc/ci-runner/org-github.token'),
                          ('org_github_token_file','GH_TOKEN'),('github_api_url','https://example.invalid')]:
            cfg=dict(self.cfg);cfg[key]=value
            with self.assertRaisesRegex(a.Refused,'live-readback'):a.verify_live_group(mod,cfg,{'phase':'enabled'})
    def test_finalize_restart_failure_after_pickup_is_recoverable_without_claim_deletion(self):
        a=self.a;self.applied();self.fail_after_launch=True
        with self.assertRaises(a.Refused):a.finalize()
        self.assertEqual(a.backup()['phase'],'finalizing');self.assertEqual(a.PAUSE.read_bytes(),a.BLOCK)
        self.assertEqual(self.inventory,{'sanctuary-ci-gh-1':'running'})
        self.fail_after_launch=False;a.finalize()
        self.assertEqual(a.backup()['phase'],'finalized');self.assertEqual(self.inventory,{'sanctuary-ci-gh-1':'running'})
    def test_finalize_crash_after_override_removal_can_retry(self):
        a=self.a;self.applied();self.running();self.crash_process('finalize','after-remove')
        self.assertEqual(a.backup()['phase'],'finalizing');self.assertFalse(a.PAUSE.exists())
        a.finalize();self.assertEqual(a.backup()['phase'],'finalized')
        self.assertEqual(self.inventory,{'sanctuary-ci-gh-1':'running'})
    def test_finalize_then_deliberate_rollback_is_supported(self):
        a=self.a;self.applied();a.finalize();self.snapshot('after');a.drain(True)
        self.snapshot('before');a.recover();a.finalize()
        self.assertEqual(a.CONFIG.read_bytes(),self.original);self.assertEqual(a.backup()['phase'],'finalized')
    def test_late_enabled_chmod_then_abrupt_exit_is_rejected_on_same_and_next_boot(self):
        a=self.a;self.drain();self.crash_process('commit','after-late-enabled-chmod')
        self.assertEqual(a.backup()['phase'],'applied')
        self.assertEqual(a.decode(a.read(a.GATE)[0])['phase'],'enabled')
        self.assertEqual(a.GATE.stat().st_mode&0o777,0o644)
        self.time+=400
        for boot in (a.BOOT_FILE.read_text().strip(),'next-boot'):
            with patch.object(a,'boot_identity',return_value=boot):
                with self.assertRaisesRegex(a.Refused,'publication'):a.check_admission_ticket()
                with patch.object(a.os,'geteuid',return_value=501),patch.object(a,'module_at') as loader:
                    with self.assertRaisesRegex(a.Refused,'publication'):a.admission_worker()
                    loader.assert_not_called()
        with self.assertRaisesRegex(a.Refused,'publication'):a.finalize()
        self.active=False;a.reconcile_recovery()
        self.assertEqual(a.backup()['phase'],'recovery-drained')
    def test_timely_enabled_publication_can_finalize_after_expiry_and_boot_change(self):
        a=self.a;self.applied();self.time+=1000
        with patch.object(a,'boot_identity',return_value='next-boot'):
            self.assertEqual(a.check_admission_ticket()['phase'],'enabled')
            a.finalize();a.finalize()
        self.assertEqual(a.backup()['phase'],'finalized');self.assertFalse(a.WRAPPER.exists())
    def test_enabled_publication_uses_real_kernel_ctime_and_binds_deadline_nonce_and_mode(self):
        a=self.a;self.applied();record=a.decode(a.read(a.GATE)[0])
        a.read=self.real_read  # Use real filesystem ctime, without virtual-clock metadata.
        def publish(expires,nonce=None,mode=0o644):
            record['expires']=expires;record['publication']={'before_ns':int(expires*1_000_000_000),
                                                           'nonce':nonce or record['nonce']}
            a.write(a.GATE,a.encoded(record));a.GATE.chmod(mode)
        publish(real_time.time()-1)
        with self.assertRaisesRegex(a.Refused,'publication-after-deadline'):a.check_admission_ticket()
        publish(real_time.time()+300)
        self.assertEqual(a.check_admission_ticket()['phase'],'enabled')
        publish(real_time.time()+300,nonce='0'*32)
        with self.assertRaisesRegex(a.Refused,'publication-proof'):a.check_admission_ticket()
        publish(real_time.time()+300,mode=0o600)
        with self.assertRaisesRegex(a.Refused,'publication-after-deadline'):a.check_admission_ticket()

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--crash-fixture':
        fixture=TransactionTests();fixture.attach_existing(sys.argv[2],json.loads(sys.argv[3]))
        a=fixture.a;action,point=sys.argv[4:6];original=a.command;original_gate=a.public_gate
        def command(args):
            if point=='before-start' and args==['systemctl','start',a.SERVICE] and (not a.PAUSE.exists() or a.PAUSE.read_bytes()!=a.BLOCK):os._exit(73)
            return original(args)
        def gate(doc,phase,**fields):
            if point=='before-enabled' and phase=='enabled':os._exit(73)
            return original_gate(doc,phase,**fields)
        original_reload=a.reload
        original_fsync_dir=a.fsync_dir
        def fsync_dir(path):
            if point=='after-late-enabled-chmod' and path==a.GATE.parent and a.GATE.exists() and a.decode(a.GATE.read_bytes()).get('phase')=='enabled':
                if a.GATE.stat().st_mode&0o777==0o600:fixture.time+=301
                else:os._exit(73)  # No controller deadline postcheck or exception/finally runs.
            return original_fsync_dir(path)
        def reload():
            if point=='after-remove' and not a.PAUSE.exists():os._exit(73)
            return original_reload()
        a.command=command;a.public_gate=gate;a.reload=reload;a.fsync_dir=fsync_dir
        {'commit':a.commit,'recover':a.recover,'finalize':a.finalize}[action]();os._exit(74)
    unittest.main()
