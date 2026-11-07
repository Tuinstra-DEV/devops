#!/usr/bin/python3 -I
"""Reviewed, fixed-purpose Sanctuary drain and combined #205/#206 transaction.

Root never opens a credential or makes a network call. The service-only cleanup
worker uses the existing credential bindings, with admission disabled. Laptop
collectors use gh GETs. No action PATCHes GitHub or cancels a workflow/job.
"""
from __future__ import annotations
import base64, contextlib, copy, fcntl, hashlib, json, os, re, stat, subprocess, sys, time, tomllib, types
from pathlib import Path
from urllib.parse import quote

PREFIX='Tuinstra-DEV/devops/.github/workflows/'
TRUSTED=PREFIX+'reusable-trusted-verification.yml@'
PINS=['e1a9f09504b903120e9b5297eff64f58a891c907','604ad2b9e636798899cb90a7aa8bdfa8d6ad2a4d',
      '23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e','a8ace2f171d7d127bbfb3a6d6b13f158f461ba98',
      'ea86c62eeac615cb292a78bf50995fed81c051ee']
OLD=TRUSTED+PINS[0]
TARGET='288dc75d5f07819cc2c41ab456140c3ee14de369'
NEW=TRUSTED+TARGET
PRODUCER=PREFIX+'reusable-gate-pr-security.yml@'+PINS[-1]
BEFORE=[TRUSTED+x for x in PINS]+[PRODUCER]
AFTER=[TRUSTED+PINS[-1],TRUSTED+PINS[1],TRUSTED+PINS[2],TRUSTED+PINS[3],NEW,PREFIX+'reusable-gate-pr-security.yml@'+TARGET]
IDS=dict(zip(('console','gate','marcel-site','notify','openairco','openairco-site','status','sudoku-tessera',
              'tracker','tuinstra-site','wodiq-app','wodiq-platform','wodiq-site'),
             (1154259119,1273386924,960069181,1304692512,1355089923,1331331146,1378644550,
              1179209966,1301969191,960069678,1238121712,1379333293,1264767351)))
IDS={'Tuinstra-DEV/'+k:v for k,v in IDS.items()}
MANAGER_SHA='5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3'
UNIT_SHA='c99fdf23b9c50971be678565db54e85d5227040ff1108ccb3622a9c2ae4d806d'
HELPER_SHA='fef9343a46c4a67ddf1e12db43aa65c2bbb5256e35bbdac14c9cb8a9ad3f1613'
SERVICE='ci-runner-manager.service'
CONFIG=Path('/etc/ci-runner/manager.toml')
MANAGER=Path('/usr/local/bin/ci-runner-manager')
UNIT=Path('/etc/systemd/system/'+SERVICE)
HELPER=Path('/usr/local/libexec/ci-runner-host-helper')
DROPIN=Path(str(UNIT)+'.d/20-org-credential.conf')
PAUSE=Path(str(UNIT)+'.d/91-controlled-cutover.conf')
SELF=Path('/usr/local/sbin/runner-controlled-cutover.py')
BLOCK=('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -I '+str(SELF)+' drain-worker\nRestart=no\n').encode()
STATE=Path('/var/lib/ci-runner-manager/state')
OVERLAY=Path('/var/lib/ci-runner/overlay')
STAGE=Path('/root/runner-cutover-stage')
BACKUP=Path('/var/backups/runner-cutover-288dc75d-v1')
JOURNAL=BACKUP/'journal.json'
UID=0
ANCHOR=Path('/')
PAT=re.compile(re.escape(PREFIX)+r'(?:reusable-trusted-verification|reusable-gate-pr-security)\.yml@[a-f0-9]{40}')
ANY_REF=re.compile(re.escape(PREFIX)+r'(?:reusable-trusted-verification|reusable-gate-pr-security)\.yml@[A-Za-z0-9._/-]+')

class Refused(Exception): pass
def require(ok, reason):
    if not ok: raise Refused(reason)
def sha(data): return hashlib.sha256(data).hexdigest()
def encoded(obj): return json.dumps(obj,sort_keys=True,separators=(',',':')).encode()+b'\n'
def unique(pairs):
    obj={}
    for k,v in pairs:
        require(k not in obj,'duplicate-json-key'); obj[k]=v
    return obj
def decode(data): return json.loads(data,object_pairs_hook=unique)

COMMAND_DEADLINE=None

@contextlib.contextmanager
def bounded_commands(seconds):
    global COMMAND_DEADLINE
    prior=COMMAND_DEADLINE; deadline=time.monotonic()+seconds; COMMAND_DEADLINE=deadline
    try: yield deadline
    finally: COMMAND_DEADLINE=prior

def command(args):
    remaining=90 if COMMAND_DEADLINE is None else min(90,COMMAND_DEADLINE-time.monotonic())
    require(remaining>0,'bounded-wait-expired-admission-paused')
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=remaining,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
    except subprocess.TimeoutExpired:
        raise Refused('command-timeout-admission-state-retained') from None
    require(p.returncode==0,'command-failed')
    return p.stdout.strip()

def secure_parent(path):
    require(path.is_absolute() and path.is_relative_to(ANCHOR),'directory-outside-anchor')
    for parent in (path,*path.parents):
        s=parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid==UID and not s.st_mode & 0o022,'unsafe-directory')
        if parent==ANCHOR: break
def read(path):
    secure_parent(path.parent)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as f:
        s=os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid==UID and s.st_nlink==1 and not s.st_mode & 0o022,'unsafe-file')
        data=f.read(2*1024*1024+1)
        require(len(data)<=2*1024*1024,'oversized-file')
        return data,s
def fsync_dir(path):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)
def write(path,data,info=None):
    secure_parent(path.parent)
    tmp=path.with_name('.runner-cutover-'+str(os.getpid())+'-'+path.name)
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            if info: os.fchown(f.fileno(),info.st_uid,info.st_gid); os.fchmod(f.fileno(),stat.S_IMODE(info.st_mode))
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path); fsync_dir(path.parent)
    finally:
        if tmp.exists(): tmp.unlink()
def mkdir(path):
    secure_parent(path.parent)
    if not path.exists(): path.mkdir(mode=0o700)
    secure_parent(path); require(stat.S_IMODE(path.stat().st_mode)==0o700,'directory-not-private')

def group_policy(group,selected):
    require(type(group) is dict and group.get('id')==4 and group.get('name')=='sanctuary-trusted-verification'
            and group.get('visibility')=='selected' and group.get('allows_public_repositories') is False
            and group.get('default') is False and group.get('inherited') is False
            and group.get('restricted_to_workflows') is True
            and group.get('workflow_restrictions_read_only') is False,'group-controls-differ')
    refs=group.get('selected_workflows')
    require(type(refs) is list and len(refs)==len(selected) and set(refs)==set(selected),'group-refs-differ')
def evidence(data,selected,removing,since=0,recovery=False):
    e=decode(data); now=time.time()
    require(set(e)=={'schema','started','finished','group','repositories','branches','active'},'evidence-shape')
    require(e['schema']==2 and type(e['started']) in (int,float) and type(e['finished']) in (int,float)
            and since<=e['started']<=e['finished']<=now+5 and now-e['started']<=300
            and e['finished']-e['started']<=600,'stale-evidence')
    group_policy(e['group'],selected)
    require(e['repositories']==IDS,'repository-selection-differs')
    require(type(e['branches']) is dict and set(e['branches'])==set(IDS),'branch-inventory-incomplete')
    for repo,branches in e['branches'].items():
        require(type(branches) is list and branches and any(b['label']=='default' for b in branches),'missing-default')
        for b in branches:
            require(set(b)=={'label','sha','refs','role_refs'} and re.fullmatch('[a-f0-9]{40}',b['sha'])
                    and type(b['refs']) is list and all(type(r) is str for r in b['refs'])
                    and type(b['role_refs']) is list and all(type(r) is str for r in b['role_refs']),'bad-branch-evidence')
            require(not set(removing)&set(b['refs']),'ref-in-use')
            if not recovery and selected==BEFORE and repo in ('Tuinstra-DEV/gate','Tuinstra-DEV/tracker'):
                require(TRUSTED+PINS[-1] not in b['role_refs'],'old-metadata-consumer')
    require(type(e['active']) is list,'active-inventory-incomplete')
    for run in e['active']:
        require(set(run)=={'repo','id','refs','status','role_refs'} and run['repo'] in IDS
                and type(run['id']) is int and run['status'] in ('queued','in_progress','waiting','requested','pending')
                and type(run['refs']) is list and type(run['role_refs']) is list,'bad-active-evidence')
        require(not set(removing)&set(run['refs']),'ref-in-active-run')
        if not recovery and selected==BEFORE and run['repo'] in ('Tuinstra-DEV/gate','Tuinstra-DEV/tracker'):
            require(TRUSTED+PINS[-1] not in run['role_refs'],'old-metadata-active-run')
    return e


NEW_MANAGER_SHA='aab54e656e1acbd261d3f28de16d53ac4a9c500276012c1e9f0897022195c40b'
NEW_HELPER_SHA='addf16e83af68ecfd99fcc080dfa93f8f04e2c8e57098f091c5688c100be6954'
ADMISSION_TIMEOUT_SECONDS=420  # Existing helper mutation 330 + 3 poll intervals.
DRAIN_TIMEOUT_SECONDS=7500    # Existing guest/lease limit 7200 + 300 cleanup budget.
POLL_SECONDS=10
OLD_BACKUP=Path('/var/backups/wodiq-pin-swap')
OLD_PAUSE=Path(str(UNIT)+'.d/90-wodiq-pin-pause.conf')
LOCK=Path('/run/ci-runner-manager/manager.lock')
FORWARD_REMOVING=(OLD,PRODUCER)
REVERSE_REMOVING=(NEW,AFTER[-1])

def module_at(path,expected):
    raw=read(path)[0]; require(sha(raw)==expected,'source-hash-drift')
    mod=types.ModuleType('reviewed_manager_'+expected[:8]); sys.modules[mod.__name__]=mod
    mod.__file__=str(path); exec(compile(raw,str(path),'exec'),mod.__dict__)
    mod.LOG.disabled=True  # Root actions emit only fixed status/reason codes.
    return mod

def installed(new=False):
    mod=module_at(MANAGER,NEW_MANAGER_SHA if new else MANAGER_SHA)
    require(sha(read(HELPER)[0])==(NEW_HELPER_SHA if new else HELPER_SHA),'helper-drift')
    require(sha(read(UNIT)[0])==UNIT_SHA,'unit-drift')
    require(read(DROPIN)[0]==b'[Service]\nLoadCredential=org_github_token:/etc/ci-runner/org-github.token\n','credential-binding-drift')
    expected={str(DROPIN)}|({str(PAUSE)} if PAUSE.exists() else set())
    require(not OLD_PAUSE.exists() and not OLD_PAUSE.is_symlink(),'previous-pause-present')
    require(set(command(['systemctl','show',SERVICE,'-p','DropInPaths','--value']).split())==expected,'unexpected-dropin')
    for prop,value in [('FragmentPath',str(UNIT)),('User','ci-runner-manager'),('Group','ci-runner-manager'),('Type','simple'),('KillMode','control-group')]:
        require(command(['systemctl','show',SERVICE,'-p',prop,'--value'])==value,'unit-property-drift')
    if PAUSE.exists(): require(read(PAUSE)[0]==BLOCK,'pause-drift')
    expected_exec=('/usr/bin/python3 -I '+str(SELF)+' drain-worker' if PAUSE.exists() else
                   '/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30')
    actual=command(['systemctl','show',SERVICE,'-p','ExecStart','--value'])
    require(actual.count('argv[]=')==1 and 'argv[]='+expected_exec+' ;' in actual,'exec-drift')
    require(command(['systemctl','show',SERVICE,'-p','Restart','--value'])==('no' if PAUSE.exists() else 'always'),'restart-policy-drift')
    return mod

def config(mod,selected):
    raw,info=read(CONFIG); cfg=mod.load_config(CONFIG)
    require(cfg.get('org_workflow_refs')==selected[:-1] and 'org_workflow_ref' not in cfg,'pin-order-drift')
    require(cfg.get('org_runner_group_id')==4 and cfg.get('org_repository_ids')==IDS
            and cfg.get('org_routing_enabled') is True and cfg.get('pool_mode')=='four'
            and cfg.get('state_dir')==str(STATE) and cfg.get('overlay_root')==str(OVERLAY)
            and cfg.get('lock_file')==str(LOCK) and cfg.get('max_lease_seconds')==7200
            and cfg.get('min_free_disk_gib')==60,'protected-policy-drift')
    roles=mod.configured_org_execution_roles(cfg)
    require(set(roles)=={'metadata-preflight','pr-security-producer'}
            and roles['metadata-preflight']['workflow_ref']==selected[-2]
            and roles['pr-security-producer']['workflow_ref']==selected[-1],'role-pin-drift')
    require(set(mod.configured_org_workflow_refs(cfg))==set(selected),'policy-union-differs')
    return raw,info,cfg

def replacement(raw,cfg,mod):
    # Preserve all unrelated bytes, including comments, credentials' paths and permissions.
    # First replace e1 with ea in the trusted array, then move the LAST ea trusted
    # occurrence and the metadata-role occurrence to the new pin. No order assumption
    # beyond the exact sudo-validated BEFORE array is accepted.
    require(raw.count(OLD.encode())==1 and raw.count((TRUSTED+PINS[-1]).encode())==2
            and raw.count(PRODUCER.encode())==1 and NEW.encode() not in raw,'ambiguous-config-literals')
    updated=raw.replace((TRUSTED+PINS[-1]).encode(),NEW.encode()).replace(OLD.encode(),(TRUSTED+PINS[-1]).encode())
    updated=updated.replace(PRODUCER.encode(),AFTER[-1].encode())
    expected=copy.deepcopy(cfg); expected['org_workflow_refs']=AFTER[:-1]
    expected['org_execution_roles']['metadata-preflight']['workflow_ref']=NEW
    expected['org_execution_roles']['pr-security-producer']['workflow_ref']=AFTER[-1]
    parsed=tomllib.loads(updated.decode()); require(parsed==expected,'unrelated-policy-change')
    mod.validate_org_config(parsed)
    return updated

def runtime_safe():
    for p in (STATE,OVERLAY):
        s=p.lstat(); require(stat.S_ISDIR(s.st_mode) and not s.st_mode & 0o022,'unsafe-runtime-directory')

def helper_busy():
    return bool(command(['systemctl','list-units','--all','--type=service','--state=activating,running,reloading,deactivating',
                         '--plain','--no-legend','ci-runner-host-helper@*.service']))

def domains():
    names=command(['virsh','--connect','qemu:///system','list','--all','--name']).splitlines()
    result={}
    for name in names:
        if not name.startswith('sanctuary-ci-'): continue
        require(re.fullmatch('sanctuary-ci-[A-Za-z0-9._-]{1,64}',name),'invalid-domain-name')
        result[name]=command(['virsh','--connect','qemu:///system','domstate',name]).lower()
    return result

def idle():
    runtime_safe()
    require(not any(p.name.startswith(('lease-','cleanup-')) or p.name.endswith('.tmp') for p in STATE.iterdir()),'lifecycle-work-present')
    require(not list(OVERLAY.iterdir()) and not domains() and not helper_busy(),'host-not-idle')

def is_active(): return command(['systemctl','show',SERVICE,'-p','ActiveState','--value'])=='active'
def reload(): command(['systemctl','daemon-reload'])
def paused(check_idle=True):
    require(read(PAUSE)[0]==BLOCK and not is_active(),'admission-not-paused')
    if check_idle: idle()
def journal(doc): write(JOURNAL,encoded(doc))
def backup():
    doc=decode(read(JOURNAL)[0]); require(doc['schema']==1,'journal-schema')
    for name,expected in [('manager',MANAGER_SHA),('helper',HELPER_SHA),('config',doc['before_sha'])]:
        require(sha(read(BACKUP/(name+'.before'))[0])==expected,'backup-drift')
    return doc

def previous_transaction():
    require(not OLD_PAUSE.exists() and not OLD_PAUSE.is_symlink(),'previous-pause-present')
    if OLD_BACKUP.exists():
        # Keep the previous transaction entirely intact; do not reuse its journal.
        prior=decode(read(OLD_BACKUP/'journal.json')[0])
        require(prior.get('phase') in ('applied','rolled-back'),'previous-transaction-unfinished')

def candidate():
    mod=module_at(STAGE/'manager.new',NEW_MANAGER_SHA)
    require(sha(read(STAGE/'helper.new')[0])==NEW_HELPER_SHA,'candidate-helper-drift')
    return mod

def take_lock():
    # Existing directory/file: never create or change manager's lifecycle lock.
    require(LOCK.parent.is_dir() and not LOCK.parent.is_symlink(),'missing-lifecycle-lock-directory')
    fd=os.open(LOCK,os.O_RDWR|os.O_NOFOLLOW)
    s=os.fstat(fd)
    require(stat.S_ISREG(s.st_mode) and s.st_nlink==1 and not s.st_mode & 0o022,'unsafe-lifecycle-lock')
    try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: os.close(fd); return None
    return fd

def quiescent(mod,cfg):
    # Every JIT POST is preceded by a durable, indefinitely quarantined claim
    # under this SAME lock. launch activates the claim under that lock. Thus no
    # quarantined entries + lock held proves there is no in-flight JIT to lose.
    history=mod.DispatchHistory(STATE).load()
    require(all(v['blocked_until']!=mod.DISPATCH_QUARANTINE_UNTIL for v in history.values()),'jit-quarantine-present')
    runtime_safe(); leases=[]
    for path in STATE.glob('lease-*.json'):
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not info.st_mode & 0o022 and info.st_size<=65536,'unsafe-lease-file')
            state=decode(stream.read(65537))
        require(type(state) is dict and state.get('phase')=='running','nonrunning-lease-present')
        leases.append(state)
    require(not any(p.name.endswith('.tmp') for p in STATE.iterdir()),'lifecycle-write-pending')
    require(not helper_busy(),'helper-active')

def stop_at_boundary(mod,cfg):
    with bounded_commands(ADMISSION_TIMEOUT_SECONDS) as deadline:
        while time.monotonic()<deadline:
            fd=take_lock()
            if fd is not None:
                try:
                    try: quiescent(mod,cfg)
                    except Refused: pass
                    else:
                        # No JIT can start between this check and stop: claims and
                        # launches require the held lock. QEMU/helper units are separate.
                        write(PAUSE,BLOCK); PAUSE.chmod(0o644); reload(); command(['systemctl','stop',SERVICE])
                        require(not is_active(),'manager-stop-failed'); return
                finally: os.close(fd)
            time.sleep(1)
        raise Refused('admission-timeout-no-service-change')

def install_cleanup_guard(mod):
    original=mod.helper
    def guarded(cfg,operation,*args,**kwargs):
        require(operation in ('list','resources','destroy'),'worker-admission-denied')
        inventory=mod.validate_inventory(decode(original(cfg,'list').stdout))
        require(all(value=='shut off' for value in inventory.values()),'worker-live-domain-refused')
        return original(cfg,operation,*args,**kwargs)
    mod.helper=guarded

def worker():
    # Executes ONLY through the existing, sandboxed systemd service account.
    # Never from root, laptop or another service with differently located credentials.
    require(os.geteuid()!=0,'worker-must-use-existing-service-user')
    require(PAUSE.read_bytes()==BLOCK,'worker-without-drain-override')
    import logging
    logging.disable(logging.CRITICAL)
    raw=MANAGER.read_bytes(); require(sha(raw) in (MANAGER_SHA,NEW_MANAGER_SHA),'worker-manager-drift')
    mod=types.ModuleType('drain_worker_manager'); sys.modules[mod.__name__]=mod
    mod.__file__=str(MANAGER); exec(compile(raw,str(MANAGER),'exec'),mod.__dict__)
    cfg=mod.load_config(CONFIG)
    install_cleanup_guard(mod)
    # main reads only the two existing systemd credentials, in the service account.
    # No config/credential/body/error is printed by this worker or its driver.
    require(mod.main(['--config',str(CONFIG),'reconcile'])==0,'worker-reconcile-refused')
    print('CLEANUP-PASS')

def drain_wait():
    with bounded_commands(DRAIN_TIMEOUT_SECONDS) as deadline:
        next_notice=0
        while time.monotonic()<deadline:
            require(read(PAUSE)[0]==BLOCK,'pause-drift'); require(not is_active(),'unexpected-worker-active')
            states=domains()
            if all(s=='shut off' for s in states.values()) and not helper_busy():
                command(['systemctl','reset-failed',SERVICE]); command(['systemctl','start',SERVICE])
                # Cleanup may perform existing GitHub DELETEs, never JIT POSTs or launch.
                # Wait for the one-shot process without printing journal contents.
                while is_active() and time.monotonic()<deadline: time.sleep(1)
                if not is_active():
                    try: idle()
                    except Refused: pass
                    else: return
            if time.monotonic()>=next_notice:
                print('DRAIN-WAIT; guests='+str(len(states))+'; no cancellation',flush=True)
                next_notice=time.monotonic()+30
            time.sleep(POLL_SECONDS)
        raise Refused('drain-timeout-admission-paused-refresh-before-and-abort')

def drain(reverse=False):
    previous_transaction(); require(not PAUSE.exists() and not PAUSE.is_symlink(),'pause-already-present')
    mod=installed(reverse); selected=AFTER if reverse else BEFORE
    raw,info,cfg=config(mod,selected)
    evidence(read(STAGE/('after.json' if reverse else 'before.json'))[0],selected,
             REVERSE_REMOVING if reverse else FORWARD_REMOVING)
    if reverse:
        doc=backup(); require(doc['phase']=='applied','wrong-rollback-phase')
        require(sha(raw)==doc['after_sha'],'config-drift')
    else:
        newmod=candidate(); updated=replacement(raw,cfg,newmod)
        require(not BACKUP.exists() and not BACKUP.is_symlink(),'transaction-already-present')
        mkdir(BACKUP)
        for name,path in [('manager',MANAGER),('helper',HELPER),('config',CONFIG)]:
            data,s=read(path); write(BACKUP/(name+'.before'),data,s)
            (BACKUP/(name+'.before')).chmod(0o600)
        # Store intended candidate; never expose protected contents in output.
        write(BACKUP/'config.after',updated,info); (BACKUP/'config.after').chmod(0o600)
        doc={'schema':1,'phase':'prepared','before_sha':sha(raw),'after_sha':sha(updated),'config_mode':stat.S_IMODE(info.st_mode),
             'config_uid':info.st_uid,'config_gid':info.st_gid,'begun':time.time()}
    doc|={'phase':'rollback-preparing' if reverse else 'preparing','begun':time.time()}; journal(doc)
    stop_at_boundary(mod,cfg)
    doc|={'phase':'rollback-draining' if reverse else 'draining','paused_at':time.time()}; journal(doc)
    try: drain_wait()
    except BaseException:
        doc['phase']='rollback-drain-interrupted' if reverse else 'drain-interrupted'; journal(doc); raise
    command(['systemctl','stop','ci-runner-host-helper.socket']); idle()
    doc['phase']='rollback-drained' if reverse else 'drained'; doc['drained_at']=time.time(); journal(doc)
    print('ROLLBACK-DRAINED' if reverse else 'DRAINED; binaries and config unchanged; refresh BEFORE before authorizing group PATCH')

def continue_drain():
    doc=backup(); phase=doc['phase']
    require(phase in ('preparing','draining','drain-interrupted','rollback-preparing','rollback-draining',
                      'rollback-drain-interrupted','recovery-drain-required'),'wrong-continue-phase')
    reverse=phase.startswith('rollback-'); recovering=phase=='recovery-drain-required'
    new=reverse or recovering; mod=installed(new); _,_,cfg=config(mod,AFTER if new else BEFORE)
    if is_active():
        # Retry a failed admission boundary with fresh evidence; the backup and
        # existing transaction are retained rather than deleted or reused blindly.
        evidence(read(STAGE/('after.json' if new else 'before.json'))[0],AFTER if new else BEFORE,
                 REVERSE_REMOVING if reverse else FORWARD_REMOVING)
        stop_at_boundary(mod,cfg)
    paused(False); drain_wait(); command(['systemctl','stop','ci-runner-host-helper.socket']); idle()
    doc['phase']='recovery-drained' if recovering else ('rollback-drained' if reverse else 'drained')
    doc['drained_at']=time.time(); doc['recovery_at']=time.time(); journal(doc)
    print('RECOVERY-DRAINED; restore BEFORE group and fresh proof, then recover' if recovering else doc['phase'].upper())

def authorize():
    doc=backup(); require(doc['phase']=='drained','wrong-phase'); paused()
    installed(); config(installed(),BEFORE)
    evidence(read(STAGE/'before.json')[0],BEFORE,FORWARD_REMOVING,doc['drained_at'])
    doc['phase']='authorized'; doc['authorized_at']=time.time(); journal(doc)
    print('PATCH-AUTHORIZED; Claude PATCHes exact AFTER and collects fresh AFTER')

def keep_paused():
    # After a failed resume, never interrupt a JIT. If active, reacquire the same
    # quiescent boundary before stopping. A refusal leaves reviewed files intact.
    if is_active():
        raw=read(MANAGER)[0]; require(sha(raw) in (MANAGER_SHA,NEW_MANAGER_SHA),'recovery-source-drift'); mod=module_at(MANAGER,sha(raw))
        cfg=mod.load_config(CONFIG); stop_at_boundary(mod,cfg)
    elif not PAUSE.exists(): write(PAUSE,BLOCK); PAUSE.chmod(0o644); reload()
    paused(False)

def start_normal():
    paused(False)
    command(['systemctl','start','ci-runner-host-helper.socket'])
    PAUSE.unlink(); fsync_dir(PAUSE.parent); reload()
    command(['systemctl','reset-failed',SERVICE]); command(['systemctl','start',SERVICE])
    time.sleep(35)  # At least one normal 30-second reconciliation poll.
    require(is_active() and command(['systemctl','is-active','ci-runner-host-helper.socket'])=='active','restart-failed')
    for prop in ('ExecMainStatus','NRestarts'):
        require(command(['systemctl','show',SERVICE,'-p',prop,'--value'])=='0','restart-not-clean')

def restore_bytes(doc):
    idle()
    for name,path in [('manager',MANAGER),('helper',HELPER),('config',CONFIG)]:
        current,info=read(path)
        allowed={'manager':(MANAGER_SHA,NEW_MANAGER_SHA),'helper':(HELPER_SHA,NEW_HELPER_SHA),
                 'config':(doc['before_sha'],doc['after_sha'])}[name]
        require(sha(current) in allowed,'unexpected-files-during-recovery')
        original=read(BACKUP/(name+'.before'))[0]
        write(path,original,info)
    installed(); config(installed(),BEFORE)

def commit():
    doc=backup(); require(doc['phase']=='authorized','wrong-phase'); paused()
    installed(); raw,info,_=config(installed(),BEFORE)
    require(sha(raw)==doc['before_sha'],'config-drift'); candidate()
    proof=evidence(read(STAGE/'after.json')[0],AFTER,FORWARD_REMOVING,doc['authorized_at'])
    require(time.time()-proof['started']<=255,'insufficient-evidence-resume-budget')
    updated=read(BACKUP/'config.after')[0]; require(sha(updated)==doc['after_sha'],'candidate-config-drift')
    doc['phase']='committing'; journal(doc)
    try:
        for path,data in [(MANAGER,read(STAGE/'manager.new')[0]),(HELPER,read(STAGE/'helper.new')[0]),(CONFIG,updated)]:
            _,s=read(path); write(path,data,s)
        installed(True); config(installed(True),AFTER)
        require(time.time()-proof['started']<=265,'insufficient-evidence-resume-budget')
        start_normal(); installed(True); config(installed(True),AFTER)
        doc['phase']='applied'; journal(doc)
        print('APPLIED; active; manager-sha256 '+NEW_MANAGER_SHA+'; helper-sha256 '+NEW_HELPER_SHA)
    except BaseException:
        keep_paused()
        try: restore_bytes(doc)
        except BaseException:
            doc['phase']='recovery-drain-required'; journal(doc)
            print('RECOVERY-DRAIN-REQUIRED; admission paused; files retained; do not PATCH BEFORE until recovery idle')
            raise
        doc['phase']='auto-restored'; doc['recovery_at']=time.time(); journal(doc)
        print('AUTO-RESTORED; admission PAUSED; Claude restores BEFORE, refreshes BEFORE, Marcel runs recover')
        raise

def recover():
    doc=backup(); require(doc['phase'] in ('auto-restored','rollback-drained','committing','authorized','drained','recovery-drained'),'wrong-recovery-phase')
    paused()
    evidence(read(STAGE/'before.json')[0],BEFORE,REVERSE_REMOVING,
             doc.get('recovery_at',doc.get('drained_at',doc['begun'])))
    restore_bytes(doc)
    try: start_normal()
    except BaseException:
        keep_paused(); doc['phase']='auto-restored'; doc['recovery_at']=time.time(); journal(doc); raise
    doc['phase']='rolled-back'; journal(doc); print('ROLLED-BACK; active; original hashes restored')

def abort():
    doc=backup(); require(doc['phase'] in ('preparing','draining','drain-interrupted','drained'),'wrong-abort-phase')
    paused(False); installed(); raw,_,_=config(installed(),BEFORE)
    require(sha(raw)==doc['before_sha'],'abort-config-drift')
    evidence(read(STAGE/'before.json')[0],BEFORE,(),doc['begun'],recovery=True)
    try: start_normal()
    except BaseException: keep_paused(); raise
    doc['phase']='aborted'; journal(doc); print('ABORTED; original admission active; running jobs untouched')

def abort_rollback():
    doc=backup(); require(doc['phase'] in ('rollback-preparing','rollback-draining','rollback-drain-interrupted','rollback-drained'),'wrong-rollback-abort-phase')
    paused(False); installed(True); raw,_,_=config(installed(True),AFTER)
    require(sha(raw)==doc['after_sha'],'rollback-abort-config-drift')
    evidence(read(STAGE/'after.json')[0],AFTER,(),doc['begun'],recovery=True)
    try: start_normal()
    except BaseException: keep_paused(); raise
    doc['phase']='applied'; journal(doc); print('ROLLBACK-ABORTED; reviewed AFTER admission active')

def isolated_metadata_refs(repo,text):
    if repo not in ('Tuinstra-DEV/gate','Tuinstra-DEV/tracker'): return []
    ruby="require 'json'; require 'yaml'; STDOUT.write(JSON.generate(YAML.safe_load(STDIN.read, aliases: true)))"
    proc=subprocess.run(['ruby','-e',ruby],input=text,capture_output=True,text=True,timeout=10)
    require(proc.returncode==0,'workflow-yaml-refused')
    doc=decode(proc.stdout); require(type(doc) is dict and type(doc.get('jobs')) is dict,'workflow-jobs-invalid')
    refs=[]
    for job in doc['jobs'].values():
        require(type(job) is dict,'workflow-job-invalid')
        ref=job.get('uses')
        if ref!=TRUSTED+PINS[-1]: continue
        inputs=job.get('with',{}); require(type(inputs) is dict,'workflow-inputs-invalid')
        if inputs.get('request-isolated-role-runner') is not False: refs.append(ref)
    return sorted(set(refs))

def collect(which):
    # Unprivileged laptop mode: gh owns auth; this code never opens credentials.
    selected=BEFORE if which=='collect-before' else AFTER; begun=time.time()
    def api(endpoint,optional=False):
        p=subprocess.run(['gh','api',endpoint],capture_output=True,text=True,timeout=40)
        if optional and p.returncode and '(HTTP 404)' in p.stderr: return None
        require(p.returncode==0,'github-get-failed'); return decode(p.stdout)
    def pages(endpoint,field=None):
        result=[]; page=1
        while True:
            obj=api(endpoint+('&' if '?' in endpoint else '?')+f'per_page=100&page={page}')
            values=obj if field is None else obj[field]; require(type(values) is list,'github-list-invalid')
            result+=values
            if len(values)<100: return result
            page+=1; require(page<11,'github-list-truncated')
    group=api('orgs/Tuinstra-DEV/actions/runner-groups/4'); group_policy(group,selected)
    repos=pages('orgs/Tuinstra-DEV/actions/runner-groups/4/repositories','repositories')
    require(all(r['private'] is True for r in repos) and {r['full_name']:r['id'] for r in repos}==IDS,'repository-selection-differs')
    result={'schema':2,'started':begun,'finished':0,'group':group,'repositories':IDS,'branches':{},'active':[]}
    cache={}
    def workflow_refs(repo,commit):
        if (repo,commit) in cache: return cache[repo,commit]
        require(type(commit) is str and re.fullmatch('[a-f0-9]{40}',commit),'invalid-source-commit')
        used=[]; role_refs=[]; files=api(f'repos/{repo}/contents/.github/workflows?ref={commit}',True) or []
        for file in files:
            if file['type']!='file' or not file['name'].endswith(('.yml','.yaml')): continue
            content=api(f'repos/{repo}/contents/{file["path"]}?ref={commit}')
            text=base64.b64decode(content['content']).decode()
            declared=ANY_REF.findall(text)
            require(all(PAT.fullmatch(r) for r in declared),'nonexact-central-workflow-ref')
            used+=declared  # Conservative: commented refs also block retirement.
            role_refs+=isolated_metadata_refs(repo,text)

        cache[repo,commit]=(sorted(set(used)),sorted(set(role_refs))); return cache[repo,commit]
    for repo in IDS:
        default=api('repos/'+repo)['default_branch']; refs={default:'default'}
        for name in ('main','develop'):
            if name not in refs and api(f'repos/{repo}/branches/{name}',True): refs[name]=name
        for pr in pages(f'repos/{repo}/pulls?state=open'): refs[pr['head']['sha']]='PR#'+str(pr['number'])
        branches=[]
        for ref,label in refs.items():
            commit=api(f'repos/{repo}/commits/{quote(ref,safe="")}')['sha']
            used,role_refs=workflow_refs(repo,commit)
            branches.append({'label':label,'sha':commit,'refs':used,'role_refs':role_refs})
        result['branches'][repo]=branches
        # Unfiltered active list catches approval-waiting/pending states too.
        for status in ('queued','in_progress','waiting','requested','pending'):
            for run in pages(f'repos/{repo}/actions/runs?status={status}','workflow_runs'):
                detail=api(f'repos/{repo}/actions/runs/{run["id"]}')
                require(detail.get('id')==run['id'] and detail.get('head_sha')==run.get('head_sha')
                        and type(detail.get('referenced_workflows')) is list,'active-run-identity-incomplete')
                refs=[r['path'] for r in detail['referenced_workflows'] if ANY_REF.fullmatch(r.get('path',''))]
                require(all(PAT.fullmatch(r) for r in refs),'nonexact-active-workflow-ref')
                # A waiting run may not yet expose a resolved reusable ref. Also
                # inspect its immutable source, not today's migrated branch tip.
                source_refs,role_refs=workflow_refs(repo,detail['head_sha'])
                if repo in ('Tuinstra-DEV/gate','Tuinstra-DEV/tracker') and TRUSTED+PINS[-1] in refs and TRUSTED+PINS[-1] not in source_refs:
                    role_refs=sorted(set(role_refs+[TRUSTED+PINS[-1]]))
                refs=sorted(set(refs+source_refs))
                result['active'].append({'repo':repo,'id':run['id'],'status':status,'refs':refs,'role_refs':role_refs})
    final=api('orgs/Tuinstra-DEV/actions/runner-groups/4'); group_policy(final,selected)
    require(final==group,'group-changed-during-collection')
    result['finished']=time.time(); require(result['finished']-begun<240,'collection-too-slow-refresh-required')
    sys.stdout.buffer.write(encoded(result))


def readback(after):
    require(os.geteuid()!=0,'readback-must-be-unprivileged')
    selected=AFTER if after else BEFORE
    def get(endpoint):
        proc=subprocess.run(['gh','api',endpoint],capture_output=True,text=True,timeout=40)
        require(proc.returncode==0,'readback-get-failed'); return decode(proc.stdout)
    group_policy(get('orgs/Tuinstra-DEV/actions/runner-groups/4'),selected)
    repositories=get('orgs/Tuinstra-DEV/actions/runner-groups/4/repositories?per_page=100')
    require(repositories.get('total_count')==len(IDS),'readback-repository-count-drift')
    repos=repositories.get('repositories'); require(type(repos) is list and len(repos)==len(IDS),'readback-repository-list-drift')
    require(all(r.get('private') is True for r in repos) and {r['full_name']:r['id'] for r in repos}==IDS,'readback-repository-selection-drift')
    print('GROUP-AFTER-MATCH' if after else 'GROUP-BEFORE-MATCH')

def main():
    require(len(sys.argv)==2,'one-fixed-action-required'); action=sys.argv[1]
    if action in ('collect-before','collect-after'):
        require(os.geteuid()!=0,'collector-must-be-unprivileged'); return collect(action)
    if action=='group-before': sys.stdout.buffer.write(encoded({'selected_workflows':BEFORE})); return
    if action=='group-after': sys.stdout.buffer.write(encoded({'selected_workflows':AFTER})); return
    if action=='drain-worker': return worker()
    if action in ('readback-before','readback-after'): return readback(action=='readback-after')
    actions={'drain':drain,'authorize':authorize,'commit':commit,'abort':abort,'recover':recover,
             'rollback-drain':lambda:drain(True),'continue-drain':continue_drain,'abort-rollback':abort_rollback}
    require(action in actions or action=='check','unknown-action')
    require(os.geteuid()==0 and sys.stdin.isatty(),'interactive-root-required')
    os.environ.clear(); os.umask(0o077); secure_parent(STAGE)
    fd=os.open('/run/runner-controlled-cutover.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    info=os.fstat(fd)
    require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode & 0o077,'unsafe-transaction-lock')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if action=='check':
        previous_transaction(); m=installed(); raw,_,cfg=config(m,BEFORE); new=candidate(); replacement(raw,cfg,new)
        evidence(read(STAGE/'before.json')[0],BEFORE,FORWARD_REMOVING)
        runtime_safe(); require(is_active(),'manager-not-active')
        print('CHECK-PASS; protected BEFORE order, roles, source hashes and candidate validated; host may be busy')
    else: actions[action]()

if __name__=='__main__':
    try: main()
    except Refused as exc:
        print('REFUSED '+str(exc),file=sys.stderr); sys.exit(1)
    except BaseException as exc:
        # No tracebacks, config, subprocess output, credentials or remote bodies.
        print('REFUSED '+type(exc).__name__,file=sys.stderr); sys.exit(1)
