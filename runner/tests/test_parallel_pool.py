import unittest
from unittest.mock import patch, Mock
import ci_runner_manager as manager
import ci_runner_host_helper as helper

class ParallelPoolTests(unittest.TestCase):
    def test_helper_keeps_per_vm_resources_but_allows_third_heavy_only_in_explicit_mode(self):
        self.assertFalse(helper.profile_has_slot({'heavy':2},'heavy','four'))
        self.assertTrue(helper.profile_has_slot({'heavy':2},'heavy','parallel'))
        self.assertFalse(helper.profile_has_slot({'heavy':3},'heavy','parallel'))
        self.assertTrue(helper.profile_has_slot({'heavy':3},'medium','parallel'))
        self.assertFalse(helper.profile_has_slot({'heavy':3,'medium':1},'medium','parallel'))
        self.assertFalse(helper.profile_has_slot({},'heavy','unknown'))
        self.assertEqual(helper.validate_resources(4,6144,'heavy'),(4,6144))
        self.assertEqual(helper.validate_resources(2,3072,'medium'),(2,3072))

    @patch.object(manager.os,'cpu_count',return_value=16)
    @patch.object(manager,'memory_total_mib',return_value=32768)
    @patch.object(manager,'memory_available_mib',return_value=20000)
    @patch.object(manager.shutil,'disk_usage',return_value=Mock(free=90*1024**3))
    def test_manager_enforces_reserve_cpu_disk_and_validated_active_allocations(self,*mocks):
        cfg={'pool_mode':'parallel','max_concurrency':5,'overlay_root':'/x','host_memory_reserve_mib':4096,'min_free_disk_gib':60}
        active=[{'profile':'heavy','vcpus':4,'memory_mib':6144,'rss_mib':5000}]*2
        self.assertEqual(manager.capacity_errors(cfg,profile='heavy',active=active),[])
        self.assertIn('host projected free memory is below the configured reserve',
          manager.capacity_errors(cfg,profile='heavy',active=active,available_override_mib=12000))
        three=active+[active[0]]
        self.assertEqual(manager.capacity_errors(cfg,profile='medium',active=three),[])
        four=three+[{'profile':'medium','vcpus':2,'memory_mib':3072,'rss_mib':3000}]
        self.assertIn('host cannot fit 14 configured runner vCPUs',manager.capacity_errors(cfg,profile='medium',active=four))
        self.assertIn('heavy runner slots are occupied',manager.capacity_errors(cfg,profile='heavy',active=three))
        with patch.object(manager.shutil,'disk_usage',return_value=Mock(free=59*1024**3)):
            self.assertIn('bounded overlay reservation would breach NVMe floor',manager.capacity_errors(cfg,profile='heavy',active=active))
        with patch.object(manager.os,'cpu_count',return_value=12):
            self.assertIn('host cannot fit 14 configured runner vCPUs',manager.capacity_errors(cfg,profile='heavy',active=active))

    def test_root_policy_must_explicitly_approve_capacity_and_rejects_other_changes(self):
        cfg={'pool_mode':'parallel','max_concurrency':5,'runner_vcpus':4,'runner_memory_mib':6144,
          'runner_label':'trusted-heavy','medium_runner_label':'trusted-medium','medium_runner_vcpus':2,
          'medium_runner_memory_mib':3072,'max_heavy':3,'max_medium':2,'host_memory_reserve_mib':4096,
          'min_free_disk_gib':60,'heavy_disk_reservation_gib':24,'medium_disk_reservation_gib':4,'storage_mode':'bounded-loop'}
        self.assertEqual(helper.policy_mode_from_mapping(cfg),'parallel')
        for key,value in [('max_heavy',4),('runner_memory_mib',4096),('host_memory_reserve_mib',0),('min_free_disk_gib',0)]:
            with self.subTest(key=key),self.assertRaises(helper.ProtocolError):helper.policy_mode_from_mapping({**cfg,key:value})

    def test_three_prs_share_slots_instead_of_draining_the_oldest_pr_first(self):
        from types import SimpleNamespace
        queue=[]
        for stamp,run in [(1,1),(2,1),(3,1),(4,2),(5,2),(6,3)]:
            queue.append(((stamp,stamp,'repo'),'organization','heavy',None,
              SimpleNamespace(candidate={'repo':'repo','run_id':run,'job_id':stamp})))
        self.assertEqual([r[-1].candidate['run_id'] for r in manager.fair_candidate_queue(queue,[])][:3],[1,2,3])
        active=[{'repo':'repo','actual_run_id':1}]*2
        self.assertEqual([r[-1].candidate['run_id'] for r in manager.fair_candidate_queue(queue,active)][:2],[2,3])

if __name__=='__main__':unittest.main()
