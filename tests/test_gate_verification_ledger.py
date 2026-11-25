import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ledger', Path(__file__).resolve().parents[1] / 'scripts/gate-verification-ledger.py')
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)

def row(component='browser', source='a', digest='b'):
    return dict(repository='Tuinstra-DEV/gate', component=component, environment='verification',
                source_commit=source*40, artifact_digest='sha256:'+digest*64, workflow_run_id='123',
                workflow_attempt='1', verified_at='2026-11-20T20:00:00Z', method='ci-provenance-and-runtime-digest')

class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'.heal').mkdir(mode=0o700)
        self.path = self.root/'.heal/verified-deployments-v7.json'
        self.original = (json.dumps({'schema':'gate.heal-deployments.v1','deployments':[row('backend','c')]})+'\n').encode()
        self.path.write_bytes(self.original); self.path.chmod(0o600)
        self.inode = self.path.stat().st_ino
        self.readback = lambda: hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_append_and_resume_preserve_original_rows_inode_mode_and_backup(self):
        new = [row(),row('nitro')]
        result = ledger.append_rows(self.root,self.path,new,self.readback)
        self.assertEqual(result['added'],2)
        self.assertEqual(Path(result['backup']).read_bytes(),self.original)
        self.assertEqual(self.path.stat().st_ino,self.inode)
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads(self.path.read_bytes())['deployments'][0],row('backend','c'))
        new[0]['verified_at']='2026-11-20T21:00:00Z'
        self.assertEqual(ledger.append_rows(self.root,self.path,new,self.readback)['added'],0)

    def test_conflicting_source_binding_or_private_field_changes_nothing(self):
        for entry in [row('backend','c','d'),dict(row(),private='synthetic')]:
            with self.assertRaises(ledger.Blocked): ledger.append_rows(self.root,self.path,[entry],self.readback)
            self.assertEqual(self.path.read_bytes(),self.original)
        ledger.append_rows(self.root,self.path,[row()],self.readback)
        snapshot=self.path.read_bytes()
        with self.assertRaisesRegex(ledger.Blocked,'binding_conflict'):
            ledger.append_rows(self.root,self.path,[row(digest='d')],self.readback)
        self.assertEqual(self.path.read_bytes(),snapshot)

    def test_duplicate_json_keys_are_not_silently_discarded(self):
        with self.assertRaisesRegex(ledger.Blocked,'duplicate_json_key'):
            ledger.strict_json('{"deployments":[],"deployments":[{}]}')

    def test_stale_worker_binding_blocks_before_backup(self):
        with self.assertRaisesRegex(ledger.Blocked,'mounted_ledger_mismatch'):
            ledger.append_rows(self.root,self.path,[row()],lambda:'0'*64)
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual(list(self.path.parent.glob('*.bak-dev54-*')),[])

    def test_deploy_marker_blocks_before_backup_or_receipt_write(self):
        (self.root/'.observability-deploying').touch()
        for apply in [True,False]:
            with self.subTest(apply=apply):
                with self.assertRaisesRegex(ledger.Blocked,'deployment_in_progress'):
                    ledger.append_rows(self.root,self.path,[row()],self.readback,apply=apply)
                self.assertEqual(self.path.read_bytes(),self.original)
                self.assertEqual(list(self.path.parent.glob('*.bak-dev54-*')),[])

    def test_failed_readback_restores_original_bytes_and_same_inode(self):
        def readback():
            return self.readback() if self.path.read_bytes()==self.original else '0'*64
        with self.assertRaisesRegex(ledger.Blocked,'append_rolled_back'):
            ledger.append_rows(self.root,self.path,[row()],readback)
        self.assertEqual(self.path.read_bytes(),self.original)
        self.assertEqual(self.path.stat().st_ino,self.inode)

    def test_busy_lock_and_symlink_are_rejected_without_mutation(self):
        with open(self.root/'.observability.lock','w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ledger.Blocked,'observability_busy'):
                ledger.append_rows(self.root,self.path,[row()],self.readback)
        link=self.path.parent/'verified-deployments-link.json';link.symlink_to(self.path)
        with self.assertRaises(ledger.Blocked):ledger.append_rows(self.root,link,[row()],self.readback)
        self.assertEqual(self.path.read_bytes(),self.original)

if __name__=='__main__':unittest.main()
