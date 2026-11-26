import json
import unittest
from unittest import mock
import ci_runner_host_helper as helper
import ci_runner_manager as manager

GIB = 1024**3

class EffectiveStorageTests(unittest.TestCase):
    def test_live_floor_is_unchanged_and_expanded_route_is_bounded(self):
        cfg = {'pool_mode': 'four', 'overlay_root': '/unused', 'max_concurrency': 4,
               'host_memory_reserve_mib': 4096, 'min_free_disk_gib': 60}
        with mock.patch.object(manager.os, 'cpu_count', return_value=16), \
                mock.patch.object(manager, 'memory_total_mib', return_value=31744), \
                mock.patch.object(manager, 'memory_available_mib', return_value=24000), \
                mock.patch.object(manager.shutil, 'disk_usage') as disk:
            for profile, extra, threshold in [('heavy', None, 86), ('medium', None, 66),
                                               ('heavy', 40, 102)]:
                for free, allowed in [(threshold * GIB - 1, False), (threshold * GIB, True)]:
                    disk.return_value = mock.Mock(free=free)
                    self.assertEqual(helper.reservation_fits(free, profile, extra), allowed)
                    self.assertEqual(not manager.capacity_errors(cfg, profile=profile, active=[],
                                                                 storage_gib=extra), allowed)

    def test_only_tessera_heavy_route_gets_40_gib(self):
        self.assertEqual(manager.storage_gib_for_dispatch('Tuinstra-DEV/sudoku-tessera',
                         'organization', 'heavy', None), 40)
        self.assertIsNone(manager.storage_gib_for_dispatch('Tuinstra-DEV/gate',
                          'organization', 'heavy', None))
        packet = {'v': 1, 'id': 'a' * 32, 'op': 'launch', 'lease': 'gh-123',
                  'vcpus': 4, 'memory_mib': 6144, 'profile': 'heavy', 'storage_gib': 40}
        self.assertEqual(helper.parse_request(json.dumps(packet).encode())['storage_gib'], 40)
        for bad in (True, 24, 41):
            with self.assertRaises(helper.ProtocolError):
                helper.parse_request(json.dumps({**packet, 'storage_gib': bad}).encode())

if __name__ == '__main__':
    unittest.main()
