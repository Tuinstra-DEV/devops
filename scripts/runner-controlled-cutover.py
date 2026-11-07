#!/usr/bin/python3 -I
"""Reviewed, fixed-purpose Sanctuary drain and combined #205/#206 transaction.

Root never opens a credential or makes a network call. The service-only cleanup
worker uses the existing credential bindings, with admission disabled. Laptop
collectors use gh GETs. No action PATCHes GitHub or cancels a workflow/job.
"""
from __future__ import annotations
import base64, contextlib, copy, fcntl, hashlib, json, os, re, stat, subprocess, sys, time, tomllib, types, uuid
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
WRAPPER=Path('/usr/local/libexec/runner-cutover-admission.py')
BLOCK=('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -I '+str(SELF)+' drain-worker\nRestart=no\n').encode()
STATE=Path('/var/lib/ci-runner-manager/state')
OVERLAY=Path('/var/lib/ci-runner/overlay')
STAGE=Path('/root/runner-cutover-stage')
TRANSACTIONS=Path('/var/backups/runner-cutover-288dc75d-v2')
ACTIVE=TRANSACTIONS/'active.json'
BACKUP=TRANSACTIONS/'unselected'
JOURNAL=BACKUP/'journal.json'
GATE=Path('/etc/ci-runner/cutover-admission.json')
READY=Path('/run/ci-runner-manager/cutover-ready.json')
BOOT_FILE=Path('/proc/sys/kernel/random/boot_id')
RUNBLOCK=('[Service]\nExecStart=\nExecStart=/usr/bin/python3 -I '+str(WRAPPER)+' admission-worker\nRestart=always\n').encode()
STABILITY_SECONDS=35
FINALIZE_STABILITY_SECONDS=300  # A separate continuous observation window after successful resume.
ORG_CREDENTIAL='/run/credentials/ci-runner-manager.service/org_github_token'
LIVE_PATHS=('/orgs/Tuinstra-DEV/actions/runner-groups/4',
            '/orgs/Tuinstra-DEV/actions/runner-groups/4/repositories?per_page=100&page=1')
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
    prior=COMMAND_DEADLINE; deadline=time.monotonic()+seconds
    if prior is not None: deadline=min(deadline,prior)
    COMMAND_DEADLINE=deadline
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
def write(path,data,info=None,*,before_replace=None,public=False):
    secure_parent(path.parent)
    tmp=path.with_name('.runner-cutover-'+str(os.getpid())+'-'+path.name)
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            if info: os.fchown(f.fileno(),info.st_uid,info.st_gid); os.fchmod(f.fileno(),stat.S_IMODE(info.st_mode))
            if public: os.fchmod(f.fileno(),0o644)
            f.write(data); f.flush(); os.fsync(f.fileno())
        if before_replace is not None: before_replace()
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
DRAIN_TIMEOUT_SECONDS=7500    # Operator wait budget; TTL is 7200, cleanup may exceed this budget.
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
    override=read(PAUSE)[0] if PAUSE.exists() else None
    require(override in (None,BLOCK,RUNBLOCK),'pause-drift')
    expected_exec=('/usr/bin/python3 -I '+str(SELF if override==BLOCK else WRAPPER)+(' drain-worker' if override==BLOCK else ' admission-worker') if override else
                   '/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30')
    actual=command(['systemctl','show',SERVICE,'-p','ExecStart','--value'])
    require(actual.count('argv[]=')==1 and 'argv[]='+expected_exec+' ;' in actual,'exec-drift')
    require(command(['systemctl','show',SERVICE,'-p','Restart','--value'])==('no' if override==BLOCK else 'always'),'restart-policy-drift')
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

def select_transaction(identifier):
    global BACKUP,JOURNAL
    require(type(identifier) is str and re.fullmatch('tx-[a-f0-9]{32}',identifier),'invalid-transaction-id')
    BACKUP=TRANSACTIONS/identifier; JOURNAL=BACKUP/'journal.json'
    secure_parent(BACKUP)

def active_doc():
    pointer=decode(read(ACTIVE)[0]); require(set(pointer)=={'transaction'},'active-pointer-shape')
    select_transaction(pointer['transaction'])
    doc=decode(read(JOURNAL)[0]); require(doc['schema']==2 and doc['transaction']==pointer['transaction'],'journal-schema')
    return doc

def backup():
    doc=active_doc()
    for name,expected in [('manager',MANAGER_SHA),('helper',HELPER_SHA),('config',doc['before_sha'])]:
        require(sha(read(BACKUP/(name+'.before'))[0])==expected,'backup-drift')
    require(sha(read(BACKUP/'config.after')[0])==doc['after_sha'],'candidate-backup-drift')
    return doc

def initialize_backup(raw,info,updated):
    mkdir(TRANSACTIONS)
    doc=active_doc() if ACTIVE.exists() else None
    if doc and doc['phase'] in ('backing-up','prepared'):
        require(doc['before_sha']==sha(raw) and doc['after_sha']==sha(updated),'partial-backup-source-drift')
    else:
        if doc:
            require(doc['phase'] in ('aborted','rolled-back') or
                    (doc['phase']=='finalized' and doc['finalize_terminal'] in ('aborted','rolled-back')),'existing-transaction-needs-recovery')
            backup()  # Verify completed rollback bytes before selecting a new ID.
            require(not is_active() or sha(read(CONFIG)[0])==doc['before_sha'],'previous-policy-drift')
        identifier='tx-'+uuid.uuid4().hex
        directory=TRANSACTIONS/identifier; directory.mkdir(mode=0o700); fsync_dir(TRANSACTIONS)
        select_transaction(identifier)
        doc={'schema':2,'transaction':identifier,'phase':'backing-up','before_sha':sha(raw),'after_sha':sha(updated),
             'config_mode':stat.S_IMODE(info.st_mode),'config_uid':info.st_uid,'config_gid':info.st_gid,'begun':time.time()}
        journal(doc)  # First durable intent, BEFORE copying any backup bytes.
        write(ACTIVE,encoded({'transaction':identifier}))
    for name,path in [('manager',MANAGER),('helper',HELPER),('config',CONFIG)]:
        data,_=read(path); target=BACKUP/(name+'.before')
        expected={'manager':MANAGER_SHA,'helper':HELPER_SHA,'config':doc['before_sha']}[name]
        require(sha(data)==expected,'backup-source-drift')
        if target.exists(): require(sha(read(target)[0])==expected,'partial-backup-drift')
        else: write(target,data)  # Private root-owned copies; never overwrite an existing backup.
    target=BACKUP/'config.after'
    if target.exists(): require(sha(read(target)[0])==doc['after_sha'],'partial-candidate-drift')
    else: write(target,updated)
    doc['phase']='prepared'; journal(doc); backup(); return doc

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
                        block_admission(); write_override(BLOCK); command(['systemctl','stop',SERVICE])
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
    previous_transaction(); require(not PAUSE.is_symlink(),'pause-symlink')
    require(not PAUSE.exists() or read(PAUSE)[0]==RUNBLOCK,'pause-already-present')
    mod=installed(reverse); selected=AFTER if reverse else BEFORE
    raw,info,cfg=config(mod,selected)
    evidence(read(STAGE/('after.json' if reverse else 'before.json'))[0],selected,
             REVERSE_REMOVING if reverse else FORWARD_REMOVING)
    if reverse:
        doc=backup(); require(doc['phase']=='applied' or
                              (doc['phase']=='finalized' and doc['finalize_terminal']=='applied'),'wrong-rollback-phase')
        require(sha(raw)==doc['after_sha'],'config-drift')
    else:
        newmod=candidate(); updated=replacement(raw,cfg,newmod)
        doc=initialize_backup(raw,info,updated)
    doc|={'phase':'rollback-preparing' if reverse else 'preparing','begun':time.time()}; journal(doc)
    stop_at_boundary(mod,cfg)
    doc|={'phase':'rollback-draining' if reverse else 'draining','paused_at':time.time()}; journal(doc)
    try: drain_wait()
    except BaseException:
        doc['phase']='rollback-drain-interrupted' if reverse else 'drain-interrupted'; journal(doc); raise
    command(['systemctl','stop','ci-runner-host-helper.socket']); idle()
    doc['phase']='rollback-drained' if reverse else 'drained'; doc['drained_at']=time.time(); journal(doc)
    print('ROLLBACK-DRAINED' if reverse else 'DRAINED; binaries and config unchanged; refresh BEFORE before authorizing group PATCH')
    print('transaction '+doc['transaction'])

def continue_drain():
    doc=backup(); phase=doc['phase']
    if phase.startswith('recovery-') or phase in ('resuming','installing'):
        return reconcile_recovery()
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

def write_override(data):
    write(PAUSE,data); PAUSE.chmod(0o644); fsync_dir(PAUSE.parent); reload()

def boot_identity():
    return BOOT_FILE.read_text().strip()

def public_gate(doc,phase,**fields):
    record={'schema':1,'transaction':doc['transaction'],'phase':phase,**fields}
    if phase=='confirmed':
        record['confirmation']={'nonce':record['nonce']}
        def confirm():
            pending={k:v for k,v in record.items() if k!='confirmation'}; pending['phase']='pending'
            require(decode(read(GATE)[0])==pending,'confirmation-pending-differs')
            require(record['boot']==boot_identity(),'confirmation-boot-changed')
            require(COMMAND_DEADLINE is not None and
                    time.monotonic()<min(COMMAND_DEADLINE,record['monotonic_expires']),'confirmation-deadline-expired')
        confirm()  # The durable pending publication precedes the decision in this process.
        # Prepare permissions and fsync BEFORE rechecking monotonic time and rename.
        write(GATE,encoded(record),before_replace=confirm,public=True)
    else:
        write(GATE,encoded(record)); GATE.chmod(0o644); fsync_dir(GATE.parent)
    return record

def wrapper_bytes():
    return ('#!/usr/bin/python3 -I\nimport runpy\nrunpy.run_path('+repr(str(SELF))+', run_name="__main__")\n').encode()

def install_wrapper():
    if WRAPPER.exists(): require(read(WRAPPER)[0]==wrapper_bytes(),'admission-wrapper-drift')
    else: write(WRAPPER,wrapper_bytes())
    WRAPPER.chmod(0o644); fsync_dir(WRAPPER.parent)

def block_admission():
    doc=active_doc(); public_gate(doc,'blocked')

def observed_files(doc):
    values=[sha(read(path)[0]) for path in (MANAGER,HELPER,CONFIG)]
    before=[MANAGER_SHA,HELPER_SHA,doc['before_sha']]
    after=[NEW_MANAGER_SHA,NEW_HELPER_SHA,doc['after_sha']]
    require(all(value in (old,new) for value,old,new in zip(values,before,after)),'unexpected-files-during-recovery')
    info=CONFIG.stat()
    require((info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))==(doc['config_uid'],doc['config_gid'],doc['config_mode']),'protected-policy-permission-drift')
    return 'before' if values==before else ('after' if values==after else 'mixed')

def check_admission_ticket(reserve=0,expected=None,bootstrap=False):
    raw,info=read(GATE); record=decode(raw)
    require(record.get('schema')==1 and record.get('phase') in
            (('probation','confirmed') if bootstrap else ('confirmed',)),'admission-blocked-unconfirmed')
    require(re.fullmatch('tx-[a-f0-9]{32}',record.get('transaction','')) is not None,'admission-transaction-invalid')
    if expected is not None: require(record==expected,'admission-ticket-changed')
    if record['phase']=='probation':
        require(record['boot']==boot_identity(),'admission-boot-changed')
        require(time.time()+reserve<record['expires'] and time.monotonic()+reserve<record['monotonic_expires'],'admission-proof-expired')
    else:
        require(record.get('terminal') in ('applied','rolled-back','aborted'),'admission-terminal-invalid')
        confirmation=record.get('confirmation')
        require(type(confirmation) is dict and set(confirmation)=={'nonce'}
                and re.fullmatch('[a-f0-9]{32}',record.get('nonce','')) is not None
                and confirmation['nonce']==record['nonce'],'admission-confirmation-proof-invalid')
        require(stat.S_IMODE(info.st_mode)==0o644,'admission-confirmation-mode-invalid')
    require((record['manager_sha'],record['helper_sha']) in ((MANAGER_SHA,HELPER_SHA),(NEW_MANAGER_SHA,NEW_HELPER_SHA)),'admission-source-invalid')
    for path,key in [(MANAGER,'manager_sha'),(HELPER,'helper_sha'),(CONFIG,'config_sha'),(SELF,'operator_sha')]:
        require(sha(read(path)[0])==record[key],'admission-file-drift')
    require(read(WRAPPER)[0]==wrapper_bytes(),'admission-wrapper-drift')
    return record

@contextlib.contextmanager
def evidence_budget(proof):
    expires=proof['started']+300
    remaining=expires-time.time(); require(remaining>STABILITY_SECONDS+10,'insufficient-evidence-resume-budget')
    with bounded_commands(remaining): yield expires

def resume_budget(proof,reserve=STABILITY_SECONDS):
    require(time.time()+reserve<proof['started']+300,'insufficient-evidence-resume-budget')
    require(COMMAND_DEADLINE is not None and time.monotonic()+reserve<COMMAND_DEADLINE,'insufficient-evidence-resume-budget')

def write_ready(record):
    # This is the only service-owned operational receipt. No root journal/config data.
    directory=READY.parent; info=directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.geteuid() and not info.st_mode & 0o077,'unsafe-receipt-directory')
    temporary=directory/('.cutover-ready-'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(encoded({'nonce':record['nonce'],'boot':boot_identity(),'opened_at':time.time()})); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,READY); fsync_dir(directory)

def ready_receipt(record):
    if not READY.exists(): return None
    fd=os.open(READY,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==STATE.stat().st_uid
                and not info.st_mode & 0o077 and info.st_size<4096,'unsafe-ready-receipt')
        data=decode(stream.read(4096))
    if data.get('nonce')!=record['nonce']: return None
    require(data.get('boot')==boot_identity() and type(data.get('opened_at')) in (int,float),'ready-receipt-invalid')
    return data

def verify_live_group(mod,cfg,record):
    # Service-account GETs using unchanged systemd credentials; never as root.
    require(cfg.get('org_github_token_file')==ORG_CREDENTIAL,'live-readback-credential-source')
    require(cfg.get('github_api_url','https://api.github.com')=='https://api.github.com','live-readback-api-origin')
    client=mod.organization_client(cfg); require(client is not None,'live-group-client-unavailable')
    original=client.request; seen=[]
    def get_only(method,path,*args,**kwargs):
        require(method=='GET' and not args and not kwargs and len(seen)<2
                and path==LIVE_PATHS[len(seen)],'live-readback-request-refused')
        seen.append(path); return original(method,path)
    client.request=get_only
    original_open=mod.open_url
    live_deadline=time.monotonic()+40
    def bounded_open(request,timeout=20):
        require(request.get_method()=='GET' and request.full_url=='https://api.github.com'+seen[-1]
                and request.data is None,'live-readback-transport-refused')
        remaining=(min(record['expires']-time.time(),record['monotonic_expires']-time.monotonic())-STABILITY_SECONDS
                   if record['phase']=='probation' else live_deadline-time.monotonic())
        require(remaining>0,'admission-proof-expired')
        return original_open(request,timeout=min(timeout,remaining))
    mod.open_url=bounded_open
    try:
        client.verify_org_group(cfg); require(tuple(seen)==LIVE_PATHS,'live-readback-incomplete')
    finally: mod.open_url=original_open; client.request=original

def install_runtime_admission_guard(mod,ticket):
    original=mod.GitHubClient.request
    def guarded(client,method,path,*args,**kwargs):
        if method=='POST' and path.endswith('/generate-jitconfig'):
            try:
                current=check_admission_ticket()
                require(current['nonce']==ticket['nonce'],'admission-generation-changed')
            except (Refused,OSError,ValueError,KeyError):
                # Existing dispatch handles this definitive pre-POST rejection by
                # removing ONLY its just-created claim; the controller never clears claims.
                raise mod.OrgPreflightRejected('controlled admission is paused') from None
        return original(client,method,path,*args,**kwargs)
    mod.GitHubClient.request=guarded

def admission_worker():
    require(os.geteuid()!=0,'admission-worker-must-use-existing-service-user')
    require(read(PAUSE)[0]==RUNBLOCK,'admission-override-missing')
    ticket=check_admission_ticket(STABILITY_SECONDS,bootstrap=True)
    mod=module_at(MANAGER,ticket['manager_sha']); cfg=mod.load_config(CONFIG)
    # Establish the original interlock before a slow read-only policy probe. A
    # controller can then stop this preflight safely without losing any JIT.
    with mod.with_lock(Path(cfg['lock_file'])): pass
    verify_live_group(mod,cfg,ticket)
    check_admission_ticket(STABILITY_SECONDS,expected=ticket,bootstrap=True)
    write_ready(ticket)
    # Run the actual daemon for the stability check, but its JIT guard accepts only
    # confirmed. Probation is bootstrap-only; neither probation nor pending dispatches.
    install_runtime_admission_guard(mod,ticket)
    mod.LOG.disabled=False  # Preserve the manager's original runtime audit logging.
    require(mod.main(['--config',str(CONFIG),'daemon','--interval','30'])==0,'admission-manager-failed')

def start_normal(doc,side,terminal,proof):
    paused(False)
    require(observed_files(doc)==side,'resume-files-differ')
    # Durable intent precedes helper start, override changes, admission and status checks.
    doc.update(phase='resuming',resume_side=side,terminal=terminal,file_set=side,nonce=uuid.uuid4().hex)
    journal(doc); block_admission()
    resume_budget(proof)
    command(['systemctl','start','ci-runner-host-helper.socket'])
    install_wrapper()
    write_override(RUNBLOCK)
    command(['systemctl','reset-failed',SERVICE])
    resume_budget(proof)  # Last root check immediately before publishing admission permission.
    ticket=public_gate(doc,'probation',nonce=doc['nonce'],boot=boot_identity(),expires=proof['started']+300,
                       monotonic_expires=COMMAND_DEADLINE,terminal=terminal,
                       manager_sha=NEW_MANAGER_SHA if side=='after' else MANAGER_SHA,
                       helper_sha=NEW_HELPER_SHA if side=='after' else HELPER_SHA,
                       config_sha=doc['after_sha'] if side=='after' else doc['before_sha'],
                       operator_sha=sha(read(SELF)[0]))
    resume_budget(proof)
    command(['systemctl','start',SERVICE])
    while ready_receipt(ticket) is None:
        resume_budget(proof)
        require(is_active(),'restart-failed-before-ready'); time.sleep(.2)
    resume_budget(proof)
    time.sleep(STABILITY_SECONDS)
    resume_budget(proof,0)
    require(is_active() and command(['systemctl','is-active','ci-runner-host-helper.socket'])=='active','restart-failed')
    for prop in ('ExecMainStatus','NRestarts'):
        require(command(['systemctl','show',SERVICE,'-p',prop,'--value'])=='0','restart-not-clean')
    require(observed_files(doc)==side,'resume-file-drift')
    installed(side=='after'); config(installed(side=='after'),AFTER if side=='after' else BEFORE)
    resume_budget(proof,0)
    doc['phase']=terminal; journal(doc)  # Terminal intent is not an admission permission.
    fields={k:v for k,v in ticket.items() if k not in ('schema','transaction','phase')}
    public_gate(doc,'pending',**fields)
    public_gate(doc,'confirmed',**fields)

def recovery_service_layout():
    # The disk override may be ahead of systemd after a crash before daemon-reload.
    # Accept only the three exact known command forms; never stop an unknown unit.
    require(sha(read(UNIT)[0])==UNIT_SHA,'recovery-unit-drift')
    require(read(DROPIN)[0]==b'[Service]\nLoadCredential=org_github_token:/etc/ci-runner/org-github.token\n','credential-binding-drift')
    for prop,value in [('FragmentPath',str(UNIT)),('User','ci-runner-manager'),('Group','ci-runner-manager'),('Type','simple'),('KillMode','control-group')]:
        require(command(['systemctl','show',SERVICE,'-p',prop,'--value'])==value,'recovery-service-drift')
    paths=set(command(['systemctl','show',SERVICE,'-p','DropInPaths','--value']).split())
    require(paths in ({str(DROPIN)},{str(DROPIN),str(PAUSE)}),'recovery-dropin-drift')
    actual=command(['systemctl','show',SERVICE,'-p','ExecStart','--value'])
    allowed=('/usr/local/bin/ci-runner-manager --config /etc/ci-runner/manager.toml daemon --interval 30',
             '/usr/bin/python3 -I '+str(SELF)+' drain-worker','/usr/bin/python3 -I '+str(WRAPPER)+' admission-worker')
    require(actual.count('argv[]=')==1 and any('argv[]='+value+' ;' in actual for value in allowed),'recovery-exec-drift')

def keep_paused():
    recovery_service_layout()
    block_admission()  # Reboot and future JIT POSTs fail closed even when safe stop is blocked.
    if is_active():
        raw=read(MANAGER)[0]; require(sha(raw) in (MANAGER_SHA,NEW_MANAGER_SHA),'recovery-source-drift')
        mod=module_at(MANAGER,sha(raw)); cfg=mod.load_config(CONFIG); stop_at_boundary(mod,cfg)
    else:
        write_override(BLOCK); command(['systemctl','stop',SERVICE])
        raw=read(MANAGER)[0]; require(sha(raw) in (MANAGER_SHA,NEW_MANAGER_SHA),'recovery-source-drift')
        mod=module_at(MANAGER,sha(raw)); history=mod.DispatchHistory(STATE).load()
        require(all(v['blocked_until']!=mod.DISPATCH_QUARANTINE_UNTIL for v in history.values()),'inactive-jit-quarantine-needs-diagnosis')
    paused(False)

def failed_resume(doc):
    # This state survives another crash or a JIT-boundary refusal inside keep_paused.
    doc.update(phase='recovery-stopping',file_set=observed_files(doc),recovery_at=time.time())
    journal(doc); block_admission()
    try: keep_paused()
    except BaseException:
        print('RECOVERY-STOP-BLOCKED; future admission blocked; current manager may remain active; run reconcile after JIT settles')
        raise
    doc['phase']='recovery-draining'; journal(doc)
    try: idle()
    except Refused:
        print('RECOVERY-DRAIN-REQUIRED; retain actual files/group; run reconcile; no guest termination')
        return
    restore_bytes(doc); doc.update(phase='auto-restored',file_set='before',recovery_at=time.time()); journal(doc)
    print('AUTO-RESTORED; admission PAUSED; restore BEFORE and fresh proof, then recover')

def restore_bytes(doc):
    paused(); observed_files(doc)
    for name,path in [('manager',MANAGER),('helper',HELPER),('config',CONFIG)]:
        _,info=read(path); write(path,read(BACKUP/(name+'.before'))[0],info)
    installed(); config(installed(),BEFORE)

def commit():
    doc=backup(); require(doc['phase']=='authorized','wrong-phase'); paused()
    installed(); raw,_,_=config(installed(),BEFORE); require(sha(raw)==doc['before_sha'],'config-drift')
    proof=evidence(read(STAGE/'after.json')[0],AFTER,FORWARD_REMOVING,doc['authorized_at'])
    try:
        with evidence_budget(proof):
            candidate(); updated=read(BACKUP/'config.after')[0]
            doc.update(phase='installing',resume_side='after',terminal='applied'); journal(doc)
            for path,data in [(MANAGER,read(STAGE/'manager.new')[0]),(HELPER,read(STAGE/'helper.new')[0]),(CONFIG,updated)]:
                resume_budget(proof); _,info=read(path); write(path,data,info)
            installed(True); config(installed(True),AFTER)
            start_normal(doc,'after','applied',proof)
        print('APPLIED; active; manager-sha256 '+NEW_MANAGER_SHA+'; helper-sha256 '+NEW_HELPER_SHA)
    except BaseException:
        if doc['phase'] in ('installing','resuming','applied'): failed_resume(doc)
        raise

def terminal_current(doc):
    side='after' if doc['phase']=='applied' else 'before'
    require(observed_files(doc)==side,'terminal-file-drift')
    installed(side=='after'); config(installed(side=='after'),AFTER if side=='after' else BEFORE)
    # Lease/domain snapshots are read-only. A busy manager remains running.
    runtime_safe(); inventory=domains()
    states=[]
    for path in STATE.glob('lease-*.json'):
        info=path.lstat(); require(stat.S_ISREG(info.st_mode) and not path.is_symlink(),'unsafe-terminal-lease')
        state=decode(path.read_bytes()); require(type(state) is dict and type(state.get('lease')) is str,'terminal-lease-invalid'); states.append(state)
    require(set(inventory)<=set('sanctuary-ci-'+state['lease'] for state in states),'untracked-terminal-domain')
    gate=check_admission_ticket()
    require(gate['phase']=='confirmed' and gate['transaction']==doc['transaction'] and gate['terminal']==doc['phase'],'terminal-admission-drift')
    require(is_active(),'terminal-manager-inactive')
    return True

def normal_service_current(doc):
    side='after' if doc['finalize_terminal']=='applied' else 'before'
    require(observed_files(doc)==side,'finalize-file-drift')
    require(not PAUSE.exists() and not PAUSE.is_symlink(),'finalize-override-present')
    mod=installed(side=='after'); config(mod,AFTER if side=='after' else BEFORE)
    require(is_active() and command(['systemctl','is-active','ci-runner-host-helper.socket'])=='active','finalize-service-inactive')
    for prop in ('ExecMainStatus','NRestarts'):
        require(command(['systemctl','show',SERVICE,'-p',prop,'--value'])=='0','finalize-service-not-clean')

def finalize():
    doc=backup()
    if doc['phase']=='finalized':
        normal_service_current(doc)
        require(not WRAPPER.exists() and not WRAPPER.is_symlink() and not GATE.exists(),'finalize-metadata-present')
        print('ALREADY-FINALIZED; original service start verified'); return
    if doc['phase']!='finalizing':
        require(doc['phase'] in ('applied','aborted','rolled-back'),'wrong-finalize-phase')
        terminal_current(doc)
        identity=[command(['systemctl','show',SERVICE,'-p',prop,'--value']) for prop in ('MainPID','ExecMainStartTimestampMonotonic')]
        require(identity[0].isdigit() and int(identity[0])>0 and identity[1].isdigit(),'finalize-service-identity-invalid')
        with bounded_commands(FINALIZE_STABILITY_SECONDS+90):
            until=time.monotonic()+FINALIZE_STABILITY_SECONDS
            while time.monotonic()<until:
                time.sleep(min(5,until-time.monotonic()))
                terminal_current(doc)
                require(all(command(['systemctl','show',SERVICE,'-p',prop,'--value'])==value for prop,value in
                            zip(('MainPID','ExecMainStartTimestampMonotonic'),identity)),'finalize-service-restarted')
                for prop in ('ExecMainStatus','NRestarts'):
                    require(command(['systemctl','show',SERVICE,'-p',prop,'--value'])=='0','finalize-service-not-clean')
        doc.update(finalize_terminal=doc['phase'],phase='finalizing',finalize_observed_at=time.time())
        journal(doc)  # Stable policy accepted before the first irreversible removal.
    side='after' if doc['finalize_terminal']=='applied' else 'before'
    require(observed_files(doc)==side,'finalize-file-drift')
    try:
        # Close at the existing JIT interlock, retaining all running guests/claims.
        keep_paused()
        require(read(PAUSE)[0]==BLOCK,'finalize-pause-drift')
        if WRAPPER.exists(): require(read(WRAPPER)[0]==wrapper_bytes(),'finalize-wrapper-drift')
        PAUSE.unlink(); fsync_dir(PAUSE.parent); reload()
        command(['systemctl','start','ci-runner-host-helper.socket'])
        command(['systemctl','reset-failed',SERVICE]); command(['systemctl','start',SERVICE])
        with bounded_commands(STABILITY_SECONDS+90):
            identity=command(['systemctl','show',SERVICE,'-p','MainPID','--value'])
            time.sleep(STABILITY_SECONDS); normal_service_current(doc)
            require(command(['systemctl','show',SERVICE,'-p','MainPID','--value'])==identity,'finalize-service-restarted')
        # The ordinary daemon is now running, with no admission wrapper or new GETs.
        if WRAPPER.exists(): WRAPPER.unlink(); fsync_dir(WRAPPER.parent)
        require(decode(read(GATE)[0])=={'schema':1,'transaction':doc['transaction'],'phase':'blocked'},'finalize-gate-drift')
        GATE.unlink(); fsync_dir(GATE.parent)
        doc['phase']='finalized'; journal(doc)
        print('FINALIZED; original service start active; wrapper and override removed; backups retained')
    except BaseException:
        # This also covers failure after the bare daemon has picked up a job.
        # Retry finalize against actual files/service; never clear a quarantine.
        if doc['phase']!='finalized':
            doc['phase']='finalizing'; journal(doc)
            keep_paused()
        raise

def reconcile_recovery():
    doc=backup()
    if doc['phase']=='finalized': return finalize()
    require(doc['phase']!='finalizing','finalization-incomplete-retry-finalize')
    if doc['phase'] in ('applied','aborted','rolled-back'):
        try: terminal_current(doc)
        except Refused: pass
        else: print('ALREADY-ACTIVE; files, service and leases checked'); return
    doc.update(phase='recovery-stopping',file_set=observed_files(doc),recovery_at=time.time())
    journal(doc); block_admission()
    keep_paused()  # May safely refuse quarantine, preserving the already-durable intent.
    doc['phase']='recovery-draining'; journal(doc)
    # Applies to BEFORE, AFTER and partial installations. Never assume new files.
    command(['systemctl','start','ci-runner-host-helper.socket'])
    drain_wait(); command(['systemctl','stop','ci-runner-host-helper.socket']); idle()
    doc.update(phase='recovery-drained',file_set=observed_files(doc),recovery_at=time.time()); journal(doc)
    print('RECOVERY-DRAINED; actual files verified; restore BEFORE group if needed; collect fresh BEFORE and run recover')

def recover():
    doc=backup()
    if doc['phase'] in ('finalizing','finalized'): return finalize()
    if doc['phase']=='rolled-back' and is_active():
        try: terminal_current(doc)
        except Refused: return reconcile_recovery()
        print('ALREADY-ROLLED-BACK; active; files and leases checked'); return
    if doc['phase'] in ('resuming','installing','recovery-stopping','recovery-draining') or not PAUSE.exists() or read(PAUSE)[0]!=BLOCK:
        return reconcile_recovery()
    require(doc['phase'] in ('auto-restored','rollback-drained','authorized','drained','recovery-drained'),'wrong-recovery-phase')
    paused()
    proof=evidence(read(STAGE/'before.json')[0],BEFORE,REVERSE_REMOVING,
                   doc.get('recovery_at',doc.get('drained_at',doc['begun'])))
    try:
        with evidence_budget(proof):
            doc.update(phase='installing',resume_side='before',terminal='rolled-back'); journal(doc)
            restore_bytes(doc); start_normal(doc,'before','rolled-back',proof)
        print('ROLLED-BACK; active; original hashes restored')
    except BaseException:
        if doc['phase'] in ('installing','resuming','rolled-back'): failed_resume(doc)
        raise

def abort():
    doc=backup()
    if doc['phase'] in ('finalizing','finalized'): return finalize()
    if doc['phase']=='aborted' and is_active():
        try: terminal_current(doc)
        except Refused: return reconcile_recovery()
        print('ALREADY-ABORTED; active'); return
    require(doc['phase'] in ('preparing','draining','drain-interrupted','drained'),'wrong-abort-phase')
    paused(False); installed(); raw,_,_=config(installed(),BEFORE); require(sha(raw)==doc['before_sha'],'abort-config-drift')
    proof=evidence(read(STAGE/'before.json')[0],BEFORE,(),doc['begun'],recovery=True)
    try:
        with evidence_budget(proof): start_normal(doc,'before','aborted',proof)
    except BaseException:
        if doc['phase'] in ('resuming','aborted'): failed_resume(doc)
        raise
    print('ABORTED; original admission active; running jobs untouched')

def abort_rollback():
    doc=backup(); require(doc['phase'] in ('rollback-preparing','rollback-draining','rollback-drain-interrupted','rollback-drained'),'wrong-rollback-abort-phase')
    paused(False); installed(True); raw,_,_=config(installed(True),AFTER); require(sha(raw)==doc['after_sha'],'rollback-abort-config-drift')
    proof=evidence(read(STAGE/'after.json')[0],AFTER,(),doc['begun'],recovery=True)
    try:
        with evidence_budget(proof): start_normal(doc,'after','applied',proof)
    except BaseException:
        if doc['phase'] in ('resuming','applied'): failed_resume(doc)
        raise
    print('ROLLBACK-ABORTED; reviewed AFTER admission active')

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
    if action=='admission-worker': return admission_worker()
    if action in ('readback-before','readback-after'): return readback(action=='readback-after')
    actions={'drain':drain,'authorize':authorize,'commit':commit,'abort':abort,'recover':recover,
             'rollback-drain':lambda:drain(True),'continue-drain':continue_drain,'abort-rollback':abort_rollback,'reconcile':reconcile_recovery,'finalize':finalize}
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
