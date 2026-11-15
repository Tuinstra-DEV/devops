from pathlib import Path
import importlib.util
import json
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('activation',ROOT/'scripts/activate-wodiq-ci-parallel.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class ActivationTests(unittest.TestCase):
    def fixture(self):
        refs=[m.OLD_WORKFLOW,m.PREFIX+'a'*40,m.PREFIX+'b'*40,m.PREFIX+'c'*40,m.PREFIX+'d'*40]
        values={'pool_mode':'four','max_concurrency':4,'max_heavy':2,'max_medium':2,
          'org_routing_enabled':True,'org_runner_group_id':4,
          'runner_vcpus':4,'runner_memory_mib':6144,'medium_runner_vcpus':2,'medium_runner_memory_mib':3072,
          'host_memory_reserve_mib':4096,'min_free_disk_gib':60,'org_workflow_refs':refs,
          'unchanged_policy':'preserve-me','org_token_path':'/private/existing-credential'}
        return ('\n'.join(k+' = '+json.dumps(v) for k,v in values.items())+'\n').encode()
    def test_capacity_and_one_immutable_ref_upgrade_is_idempotent_and_preserves_all_other_fields(self):
        import tomllib
        old=self.fixture();new=m.candidate_config(old,'e'*40);again=m.candidate_config(new,'e'*40)
        self.assertEqual(new,again)
        before,after=tomllib.loads(old.decode()),tomllib.loads(new.decode())
        self.assertEqual(after['pool_mode'],'parallel');self.assertEqual(after['max_concurrency'],5)
        self.assertEqual(after['max_heavy'],3)
        self.assertTrue(set(before['org_workflow_refs']) < set(after['org_workflow_refs']))
        self.assertEqual(after['org_workflow_refs'][-1],before['org_workflow_refs'][-1])
        for key in set(before)-{'pool_mode','max_concurrency','max_heavy','org_workflow_refs'}:
            self.assertEqual(before[key],after[key])
    def test_unknown_policy_and_credential_or_resource_expansion_are_rejected(self):
        for original in [self.fixture().replace(b'"four"',b'"legacy"'),
          self.fixture().replace(b'runner_memory_mib = 6144',b'runner_memory_mib = 12288'),
          self.fixture().replace(b'min_free_disk_gib = 60',b'min_free_disk_gib = 1'),
          self.fixture().replace(b'org_runner_group_id = 4',b'org_runner_group_id = 1')]:
            with self.assertRaises(AssertionError):m.candidate_config(original,'e'*40)
        with self.assertRaises(AssertionError):m.candidate_config(self.fixture(),'main')
    def test_no_vm_stopping_or_key_provisioning_actions_exist_in_activation(self):
        source=(ROOT/'scripts/activate-wodiq-ci-parallel.py').read_text()
        self.assertNotIn("'destroy'",source);self.assertNotIn("'undefine'",source)
        self.assertNotIn('authorized_keys',source);self.assertNotIn('generate-jit',source)
        self.assertIn('require_drained()',source)

if __name__=='__main__':unittest.main()
