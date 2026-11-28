"""Append only verified Gate SDK receipts; preserve the mounted inode and all history."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import uuid

class Blocked(Exception):
    pass

FIELDS = {
    'repository': r'Tuinstra-DEV/[A-Za-z0-9_.-]{1,100}', 'component': r'[a-z][a-z0-9-]{0,31}',
    'environment': r'production|verification', 'source_commit': r'[a-f0-9]{40}',
    'artifact_digest': r'sha256:[a-f0-9]{64}', 'workflow_run_id': r'[1-9][0-9]{0,19}',
    'workflow_attempt': r'[1-9][0-9]{0,5}', 'verified_at': r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',
    'method': r'ci-provenance-and-runtime-digest',
}
def require(ok, category):
    if not ok: raise Blocked(category)

def strict_json(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            require(key not in result,'duplicate_json_key');result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=unique)

def validate(rows):
    require(isinstance(rows,list) and len(rows)<=256,'ledger_rows_invalid')
    found={}
    for row in rows:
        require(isinstance(row,dict) and set(row)==set(FIELDS),'receipt_schema_invalid')
        require(all(isinstance(row[k],str) and re.fullmatch(pattern,row[k]) for k,pattern in FIELDS.items()),'receipt_fields_invalid')
        try: datetime.strptime(row['verified_at'],'%Y-%m-%dT%H:%M:%SZ')
        except ValueError: raise Blocked('receipt_timestamp_invalid') from None
        key=tuple(row[k] for k in ['repository','component','environment','source_commit'])
        require(key not in found,'duplicate_binding')
        found[key]=row
    return found

def write_fd(fd, data):
    pos=0
    while pos<len(data):
        size=os.pwrite(fd,data[pos:],pos)
        require(size>0,'ledger_write_failed');pos+=size
    os.ftruncate(fd,len(data));os.fsync(fd)

def append_rows(root, path, proposed, mounted_digest, *, apply=True):
    root=Path(root);path=Path(path)
    require(not root.is_symlink() and not (root/'.heal').is_symlink() and not path.is_symlink() and re.fullmatch(r'verified-deployments[A-Za-z0-9_.-]*\.json',path.name) and path.parent.resolve()==(root/'.heal').resolve(),'ledger_path_invalid')
    info=path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid() and stat.S_IMODE(info.st_mode)==0o600 and info.st_size<=262144,'ledger_permissions_invalid')
    validate(proposed)
    require(0<len(proposed)<=2 and all(r['repository']=='Tuinstra-DEV/gate' and r['component'] in ['browser','nitro'] and r['environment']=='verification' for r in proposed),'verification_scope_invalid')
    with ExitStack() as stack:
        for lock in [root/'.observability.lock',path.with_name(path.name+'.sync.lock')]:
            fd=os.open(lock,os.O_RDWR|os.O_NOFOLLOW|(os.O_CREAT if apply else 0),0o600)
            stack.callback(os.close,fd)
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise Blocked('observability_busy') from None
        require(not (root/'.observability-deploying').exists(),'deployment_in_progress')
        old=path.read_bytes();require(len(old)<=262144,'ledger_capacity_exceeded');original=strict_json(old)
        require(set(original)=={'schema','deployments'} and original['schema']=='gate.heal-deployments.v1','ledger_schema_invalid')
        existing=original['deployments'];found=validate(existing);new=[]
        for row in proposed:
            key=tuple(row[k] for k in ['repository','component','environment','source_commit'])
            if key in found:
                require(all(found[key][k]==v for k,v in row.items() if k!='verified_at'),'binding_conflict')
            else:new.append(row)
        updated=existing+new;validate(updated)
        data=(json.dumps({'schema':'gate.heal-deployments.v1','deployments':updated},indent=2)+'\n').encode()
        require(len(data)<=262144,'ledger_capacity_exceeded')
        digest=lambda b:hashlib.sha256(b).hexdigest()
        require(mounted_digest()==digest(old),'mounted_ledger_mismatch')
        if not new or not apply:return {'read_only':not apply,'added':len(new),'previous_rows':len(existing),'total_rows':len(updated)}
        backup=path.with_name(path.name+'.bak-dev54-sdk-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(old);stream.flush();os.fsync(stream.fileno())
            directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
        except Exception:raise Blocked('backup_failed') from None
        require(backup.read_bytes()==old,'backup_readback_failed')
        fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
        try:
            current=os.fstat(fd)
            require((current.st_ino,current.st_uid,current.st_mode,current.st_nlink)==(info.st_ino,info.st_uid,info.st_mode,info.st_nlink) and path.read_bytes()==old,'ledger_changed_before_write')
            try:
                write_fd(fd,data)
                require(path.read_bytes()==data and mounted_digest()==digest(data),'append_readback_failed')
            except Exception:
                write_fd(fd,old)
                require(path.read_bytes()==old and mounted_digest()==digest(old),'rollback_readback_failed')
                raise Blocked('append_rolled_back') from None
        finally:os.close(fd)
        return {'read_only':False,'added':len(new),'previous_rows':len(existing),'total_rows':len(updated),
                'backup':str(backup),'backup_sha256':digest(old),'mounted_sha256':digest(data),'inode_preserved':path.stat().st_ino==info.st_ino}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('receipts');parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:
        root=Path('/mnt/ssd1000-01/projects/gate/production')
        run=lambda argv:subprocess.check_output(argv,stderr=subprocess.PIPE,text=True)
        mounts=json.loads(run(['docker','inspect','--format','{{json .Mounts}}','gate-messenger-worker-1']))
        paths=[m['Source'] for m in mounts if m['Destination']=='/run/gate-heal/deployments.json']
        require(len(paths)==1,'mounted_ledger_not_unique')
        mounted=lambda:run(['docker','exec','gate-messenger-worker-1','php','-r','echo hash_file("sha256", "/run/gate-heal/deployments.json");']).strip()
        print(json.dumps(append_rows(root,Path(paths[0]),strict_json(Path(args.receipts).read_text()),mounted,apply=args.apply)))
    except Blocked as error:print(json.dumps({'blocked':str(error)}));raise SystemExit(1)
    except Exception as error:print(json.dumps({'blocked':'verification_ledger_failed','error_class':type(error).__name__}));raise SystemExit(1)
