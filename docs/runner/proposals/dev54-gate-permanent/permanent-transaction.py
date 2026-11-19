#!/usr/bin/env python3
"""Permanent Gate activation; reuse the unchanged, interruption-tested recovery engine."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tomllib

BASE_SHA256 = '918bdbed1676eceb72ca4d6fdb629c9c86b4dfff49d7a5adf294e543d944218b'
ROLES = {'gate-backend':'reusable-gate-ci.yml','gate-release-build':'reusable-gate-release-ci.yml','gate-release-verify':'reusable-gate-release-ci.yml'}


def load(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);sys.modules[name]=value;spec.loader.exec_module(value)
    return value


def render_roles(before, pin):
    if not re.fullmatch('[a-f0-9]{40}',pin): raise ValueError('invalid_permanent_pin')
    data=before
    for role, workflow in ROLES.items():
        cfg=tomllib.loads(data.decode());roles=cfg.get('org_execution_roles',{})
        if role in roles: raise ValueError('permanent_role_already_present')
        policy={'workflow_ref':f'Tuinstra-DEV/devops/.github/workflows/{workflow}@{pin}',
                'repositories':['Tuinstra-DEV/gate'],'events':['pull_request'],'actors':[{'login':'marcel-tuinstra','type':'User'}]}
        expected=cfg|{'org_execution_roles':roles|{role:policy}}
        fields='workflow_ref = '+json.dumps(policy['workflow_ref'])+', repositories = ["Tuinstra-DEV/gate"], events = ["pull_request"], actors = [{ login = "marcel-tuinstra", type = "User" }]'
        block=('\n[org_execution_roles.'+role+']\n'+fields.replace(', repositories','\nrepositories').replace(', events','\nevents').replace(', actors','\nactors')+'\n').encode()
        def matches(candidate):
            try:return tomllib.loads(candidate.decode())==expected
            except tomllib.TOMLDecodeError:return False
        if matches(data+block): data+=block;continue
        if len(data)>262144 or data.count(b'}')>1024: raise ValueError('unsupported_role_layout')
        insertion=((', ' if roles else '')+role+' = { '+fields+' }').encode()
        candidates=[data[:i]+insertion+data[i:] for i,b in enumerate(data) if b==ord('}') and matches(data[:i]+insertion+data[i:])]
        if len(candidates)!=1: raise ValueError('unsupported_role_layout')
        data=candidates[0]
    return data


def configure(package):
    path=package/'gate-pilot-transaction.py'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=BASE_SHA256: raise ValueError('rollback_engine_hash_changed')
    base=load(path,'permanent_recovery_engine')
    manifest=json.loads((package/'manager-manifest.json').read_text())
    hashes=json.loads((package/'package-inputs.json').read_text())
    required={'manager-manifest.json','plan.json','request.json','group-before.json','group-after.json','repositories-before.json','operator_check.py','read_effective.py','image-maintenance-update-result.json','ci_runner_manager.candidate.py'}
    if set(hashes)!=required: raise ValueError('permanent_package_inputs_differ')
    for name,digest in hashes.items():
        if hashlib.sha256((package/name).read_bytes()).hexdigest()!=digest: raise ValueError('permanent_input_hash_changed')
    base.PACKAGE=package;base.INPUT_HASHES=hashes;base.STAGED_MANAGER=package/'ci_runner_manager.candidate.py'
    base.NEW_MANAGER_HASH=manifest['target_sha256'];base.OLD_MANAGER_HASH=manifest['source_sha256']
    base.BACKUP_ROOT=Path('/var/backups/dev54-gate-permanent')
    base.JOURNAL=base.BACKUP_ROOT/'active.json';base.STATUS=package/'status.json'
    return base,manifest


def verify_recovery_inputs(base,state,live_before=False):
    backup=Path(state['backup']);before=base.load_backup_before(backup,state['before'])
    if before!=state['before']:raise base.Blocked('recovery_metadata_differs')
    for key in ('manager','config'):
        for field in ('uid','gid','mode'):
            value=before.get(key+'_'+field)
            if type(value) is not int or value<0 or (field=='mode' and value>0o7777):raise base.Blocked('recovery_metadata_incomplete')
    for name,key in [('manager.before','manager'),('manager.toml.before','config')]:
        base.secure_regular(backup/name,before[key+'_sha256'])
        if stat.S_IMODE((backup/name).lstat().st_mode)!=0o600:raise base.Blocked('recovery_backup_mode_differs')
    for path in (base.JOURNAL,backup/'transaction.json'):
        if json.loads(base.secure_regular(path))!=state:raise base.Blocked('recovery_journal_readback_differs')
    if live_before:base.verify_restored_files(before)
    return before


def check_merged(client,request,pin):
    for repo,number,key in [('devops',220,'provider'),('gate',444,'gate')]:
        pr=client.request('GET',f'/repos/Tuinstra-DEV/{repo}/pulls/{number}')
        if not pr.get('merged') or pr['head']['sha']!=request[key]['head'] or pr['merge_commit_sha']!=request[key]['merge']:
            raise ValueError('reviewed_pr_merge_differs')
    branch=client.request('GET','/repos/Tuinstra-DEV/gate/branches/develop')
    if not branch.get('protected') or branch['commit']['sha']!=request['gate']['merge']:raise ValueError('gate_develop_changed')
    comparison=client.request('GET',f'/repos/Tuinstra-DEV/devops/compare/{pin}...main')
    if comparison.get('status') not in {'ahead','identical'} or comparison.get('behind_by')!=0:raise ValueError('provider_pin_not_retained_on_main')


def validate_policy(base,client,plan,cfg):
    group,repos=base.read_group(client,cfg)
    expected=plan['runner_group_before']
    if not base.group_policy_matches(group) or group['selected_workflows']!=expected['selected_workflows'] or len(group['selected_workflows'])!=6:
        raise base.Blocked('permanent_original_group_changed')
    if base.repository_map(repos)!=cfg['org_repository_ids'] or sorted(cfg['org_repository_ids'].values())!=sorted(plan['repository_ids_unchanged']):
        raise base.Blocked('permanent_repository_selection_changed')
    return group,repos


def activate(base,manifest,package):
    if base.JOURNAL.exists():raise base.Blocked('permanent_journal_already_present_use_status_or_rollback')
    if base.sha256_file(base.MANAGER)!=base.OLD_MANAGER_HASH:raise base.Blocked('effective_manager_baseline_changed')
    base.verify_candidate_and_source()
    original=base.secure_regular(base.CONFIG);cfg=base.parse_manager_config(original)
    old=base.load_module(base.MANAGER,'permanent_old_manager');old.validate_org_config(cfg)
    checker=base.load_operator_checker(package)
    token,client=base.existing_manager_client(cfg,checker,old)
    plan=json.loads((package/'plan.json').read_text());request=json.loads((package/'request.json').read_text())
    check_merged(client,request,manifest['workflow_pin']);validate_policy(base,client,plan,cfg)
    if set(old.configured_org_workflow_refs(cfg))!=set(plan['runner_group_before']['selected_workflows']):raise base.Blocked('original_manager_group_disagree')
    after=render_roles(original,manifest['workflow_pin']);after_cfg=base.parse_manager_config(after)
    candidate=base.load_module(base.STAGED_MANAGER,'permanent_new_manager');candidate.validate_org_config(after_cfg)
    if set(candidate.configured_org_workflow_refs(after_cfg))!=set(plan['runner_group_after']['selected_workflows']):raise base.Blocked('permanent_roles_and_group_disagree')
    image,evidence=checker.gate_candidate_image(package);checker.candidate_image_php_check(image)
    if evidence['sha256']!=base.CANDIDATE_HASH:raise base.Blocked('candidate_evidence_changed')
    base.wait_until_empty(base.MAX_WAIT_SECONDS,'waiting_for_empty_permanent_window')
    # Never snapshot selector/helper drift as an approved recovery baseline.
    base.verify_candidate_and_source()
    if base.secure_regular(base.CONFIG)!=original:
        raise base.Blocked('prechange_baseline_drift')
    backup,before=base.make_backup();info=base.CANDIDATE_SOURCE.stat()
    state={'phase':'prepared','backup':str(backup),'before':before,'config_after_sha256':hashlib.sha256(after).hexdigest(),
           'sealed_image_preexisting':base.NEW_IMAGE.exists(),
           'candidate_promotion':{'mode':'not_started','source':str(base.CANDIDATE_SOURCE),'source_uid':info.st_uid,'source_gid':info.st_gid,
                                  'source_mode':stat.S_IMODE(info.st_mode),'source_atime_ns':info.st_atime_ns,'source_mtime_ns':info.st_mtime_ns,'source_size':info.st_size}}
    base.write_json(backup/'transaction.json',state);base.write_json(base.JOURNAL,state)
    verify_recovery_inputs(base,state,live_before=True)
    mutated=False
    try:
        while True:
            base.wait_until_empty(base.MAX_WAIT_SECONDS,'waiting_for_empty_permanent_window')
            if base.secure_regular(base.CONFIG)!=original or base.sha256_file(base.MANAGER)!=base.OLD_MANAGER_HASH:raise base.Blocked('prechange_baseline_drift')
            check_merged(client,request,manifest['workflow_pin']);validate_policy(base,client,plan,cfg)
            base.verify_candidate_and_source()
            base.safe_command(['systemctl','stop',base.SERVICE]);base.wait_service(False)
            try:base.ensure_empty()
            except base.Blocked as error:
                base.safe_command(['systemctl','start',base.SERVICE]);base.wait_service(True)
                if error.code!='runner_window_not_empty':raise
                continue
            if base.secure_regular(base.CONFIG)!=original or base.sha256_file(base.MANAGER)!=base.OLD_MANAGER_HASH:raise base.Blocked('prechange_baseline_drift')
            validate_policy(base,client,plan,cfg)
            # The service-down readback closes the admission/waiting race before writes.
            base.verify_candidate_and_source()
            break
        # Backup readback was completed before the first mutation. Journal-bound
        # recovery accepts each old/new partial state, including service-down.
        mutated=True
        state['candidate_promotion']=base.prepare_sealed_candidate();base.write_json(base.JOURNAL,state)
        base.patch_group(client,plan['runner_group_after']['selected_workflows'])
        base.atomic_write(base.MANAGER,base.read_staged_manager(),before['manager_uid'],before['manager_gid'],before['manager_mode'])
        base.atomic_write(base.CONFIG,after,before['config_uid'],before['config_gid'],before['config_mode'])
        base.atomic_selector(base.NEW_IMAGE)
        if base.sha256_file(base.CONFIG)!=state['config_after_sha256']:raise base.Blocked('new_config_readback_differs')
        for path,key in ((base.MANAGER,'manager'),(base.CONFIG,'config')):
            info=path.lstat()
            if (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))!=(before[key+'_uid'],before[key+'_gid'],before[key+'_mode']):raise base.Blocked('new_file_metadata_differs')
        base.safe_command(['systemctl','start',base.SERVICE]);base.wait_service(True)
        base.verify_running_state(True,backup,candidate);client.verify_org_group(after_cfg)
        state['phase']='active';base.write_json(base.JOURNAL,state)
        result={'phase':'active','recovery_metadata_verified':True,'manager_sha256':manifest['target_sha256'],
                'image_sha256':base.CANDIDATE_HASH,'group_refs':8,'new_gate_roles':3,'other_settings_unchanged':True,'production_activated':False}
        base.write_json(base.STATUS,result,0o644)
        return result
    except BaseException:
        if mutated:
            if base.service_state()['ActiveState']=='active':base.stop_manager_when_empty('permanent_activation_failure_waiting_for_idle')
            base.rollback_stopped_transaction(package,state,backup,before)
        elif base.service_state()['ActiveState']!='active':
            base.safe_command(['systemctl','start',base.SERVICE]);base.wait_service(True)
        raise


def main():
    package=Path(__file__).resolve().parent
    if os.geteuid()!=0:raise ValueError('existing_operator_sudo_required')
    base,manifest=configure(package)
    fd=os.open('/run/lock/dev54-gate-permanent.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError('permanent_lock_unsafe')
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    mode=sys.argv[1] if len(sys.argv)==2 else ''
    if mode=='activate':
        if not sys.stdin.isatty():raise ValueError('existing_operator_tty_required')
        return activate(base,manifest,package)
    if mode=='rollback':
        base.rollback_only(package)
        return {'phase':'rolled_back','manager_config_selector_and_group_restored':True,'production_activated':False}
    if mode=='status':return json.loads(base.secure_regular(base.STATUS))
    raise ValueError('unsupported_permanent_action')

if __name__=='__main__':
    try:print(json.dumps(main(),separators=(',',':')))
    except BaseException as error:
        code=getattr(error,'code',None)
        if code is None:code=str(error) if isinstance(error,ValueError) and re.fullmatch('[a-z0-9_]+',str(error)) else type(error).__name__
        print(json.dumps({'phase':'blocked','blocker':code}));raise SystemExit(1)
