"""Tessera's full build needs more space without changing other runners."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ci_runner_host_helper as helper
import ci_runner_manager as manager

GIB = 1024**3


class TesseraStorageTests(unittest.TestCase):
    def test_only_trusted_tessera_heavy_dispatch_gets_the_larger_volume(self):
        select = manager.storage_gib_for_dispatch
        self.assertEqual(select('Tuinstra-DEV/sudoku-tessera', 'organization', 'heavy', None), 40)
        for repo, scope, profile, role in [
            ('Tuinstra-DEV/gate', 'organization', 'heavy', None),
            ('Tuinstra-DEV/sudoku-tessera', 'repository', 'heavy', None),
            ('Tuinstra-DEV/sudoku-tessera', 'organization', 'medium', None),
            ('Tuinstra-DEV/sudoku-tessera', 'organization', 'heavy', 'some-role'),
        ]:
            with self.subTest(repo=repo, scope=scope, profile=profile, role=role):
                self.assertIsNone(select(repo, scope, profile, role))

    def test_extended_heavy_reservation_keeps_live_host_floor(self):
        self.assertEqual(helper.PROFILE_DISK_GIB, {'heavy': 24, 'medium': 4})
        self.assertFalse(helper.reservation_fits(102 * GIB - 1, 'heavy', 40))
        self.assertTrue(helper.reservation_fits(102 * GIB, 'heavy', 40))
        self.assertEqual(helper.post_reservation_floor_gib('heavy', 40), 62)
        self.assertTrue(helper.reservation_fits(60 * GIB, 'heavy'))
        cfg = {'pool_mode': 'four', 'overlay_root': '/unused', 'max_concurrency': 4,
               'host_memory_reserve_mib': 4096, 'min_free_disk_gib': 60}
        with mock.patch.object(manager.os, 'cpu_count', return_value=16), \
                mock.patch.object(manager, 'memory_total_mib', return_value=31744), \
                mock.patch.object(manager, 'memory_available_mib', return_value=24000), \
                mock.patch.object(manager.shutil, 'disk_usage') as disk:
            for free, allowed in ((102 * GIB - 1, False), (102 * GIB, True)):
                disk.return_value = mock.Mock(free=free)
                self.assertEqual(not manager.capacity_errors(cfg, profile='heavy', active=[], storage_gib=40), allowed)

    def test_helper_protocol_rejects_unapproved_storage_requests(self):
        request = {'v': 1, 'id': 'a' * 32, 'op': 'launch', 'lease': 'gh-123',
                   'vcpus': 4, 'memory_mib': 6144, 'profile': 'heavy', 'storage_gib': 40}
        self.assertEqual(helper.parse_request(json.dumps(request).encode())['storage_gib'], 40)
        for replacement in (0, 24, 41, True, '40'):
            with self.subTest(storage_gib=replacement), self.assertRaises(helper.ProtocolError):
                helper.parse_request(json.dumps({**request, 'storage_gib': replacement}).encode())
        with self.assertRaises(helper.ProtocolError):
            helper.parse_request(json.dumps({**request, 'profile': 'medium', 'vcpus': 2, 'memory_mib': 3072}).encode())

    def test_tessera_checks_expanded_capacity_before_any_jit_registration(self):
        repo = 'Tuinstra-DEV/sudoku-tessera'
        ref = manager.ORG_WORKFLOW_PREFIX + 'a' * 40
        with tempfile.TemporaryDirectory() as directory:
            cfg = {'pool_mode': 'four', 'max_concurrency': 4, 'state_dir': directory,
                   'lock_file': str(Path(directory) / 'lock'), 'runner_label': 'trusted-heavy',
                   'org_repository_ids': {repo: 1}, 'org_runner_group_id': 4}
            client = mock.Mock()
            client.candidate_jobs.return_value = [{'repo': repo, 'run_id': 2, 'job_id': 3,
                                                   'workflow_ref': ref}]
            with mock.patch.object(manager, 'org_routing_enabled', return_value=True), \
                    mock.patch.object(manager, 'validate_org_config'), \
                    mock.patch.object(manager, 'configured_trusted_workflow_refs', return_value={ref}), \
                    mock.patch.object(manager, 'pre_jit_capacity_errors', return_value=['disk']) as capacity:
                self.assertFalse(manager.dispatch_once(cfg, client, profile='heavy', scope='organization'))
            self.assertEqual(capacity.call_args.args[2:], ('heavy', 40))
            client.generate_jit.assert_not_called()

    def test_verified_tessera_launch_passes_exact_expansion_to_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = {'pool_mode': 'four', 'max_concurrency': 4, 'state_dir': directory,
                   'lock_file': str(Path(directory) / 'lock')}
            metadata = {'repo': 'Tuinstra-DEV/sudoku-tessera', 'scope': 'organization'}
            def respond(_cfg, op, *args, **kwargs):
                return mock.Mock(stdout=b'{}' if op == 'list' else b'[]')
            with mock.patch.object(manager, 'helper', side_effect=respond) as host, \
                    mock.patch.object(manager, 'capacity_errors', return_value=[]) as capacity:
                manager.launch(cfg, 'gh-123', b'e30=', metadata=metadata, profile='heavy')
            self.assertEqual(capacity.call_args.kwargs['storage_gib'], 40)
            launch = next(call for call in host.call_args_list if call.args[1] == 'launch')
            self.assertEqual(launch.kwargs['storage_gib'], 40)
