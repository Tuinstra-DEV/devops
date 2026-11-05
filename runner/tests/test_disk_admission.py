"""Exact prelaunch threshold, allocation races and bounded storage safety."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import ci_runner_manager as manager
import ci_runner_host_helper as helper

GIB = 1024**3


class DiskAdmissionTests(unittest.TestCase):
    def test_manager_and_helper_accept_exactly_60_gib_before_either_pool_launch(self):
        cfg = {'pool_mode': 'four', 'overlay_root': '/unused', 'max_concurrency': 4,
               'host_memory_reserve_mib': 4096, 'min_free_disk_gib': 60}
        with mock.patch.object(manager.os, 'cpu_count', return_value=16), \
                mock.patch.object(manager, 'memory_total_mib', return_value=31744), \
                mock.patch.object(manager, 'memory_available_mib', return_value=24000), \
                mock.patch.object(manager.shutil, 'disk_usage') as disk:
            for profile in ('heavy', 'medium'):
                for free, allowed in ((60 * GIB - 1, False), (60 * GIB, True),
                                      (60 * GIB + 1, True)):
                    disk.return_value = mock.Mock(free=free)
                    with self.subTest(profile=profile, free=free):
                        self.assertEqual(not manager.capacity_errors(cfg, profile=profile, active=[]), allowed)
                        self.assertEqual(helper.reservation_fits(free, profile), allowed)

    def test_24_gib_heavy_reservation_leaves_36_and_blocks_a_second_launch(self):
        self.assertEqual(helper.PROFILE_DISK_GIB, {'heavy': 24, 'medium': 4})
        self.assertEqual(helper.post_reservation_floor_gib('heavy'), 34)
        self.assertEqual(helper.post_reservation_floor_gib('medium'), 54)
        self.assertFalse(helper.reservation_fits(36 * GIB, 'heavy'))
        self.assertFalse(helper.reservation_fits(56 * GIB, 'medium'))
        self.assertFalse(helper.reservation_fits(60 * GIB, 'unknown'))

    def test_host_writer_race_after_fallocate_aborts_before_mkfs_mount_or_vm(self):
        for profile, remaining in (('heavy', 34), ('medium', 54)):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                backing = directory / 'bounded.img'
                real_stat = Path.stat
                def file_stat(path, *args, **kwargs):
                    if path == backing:
                        size = helper.PROFILE_DISK_GIB[profile] * GIB
                        return mock.Mock(st_size=size, st_blocks=size // 512)
                    return real_stat(path, *args, **kwargs)
                with mock.patch.object(helper, 'bounded_paths', return_value=(directory, backing, directory / 'work')), \
                        mock.patch.object(Path, 'stat', autospec=True, side_effect=file_stat), \
                        mock.patch.object(helper.shutil, 'disk_usage', side_effect=[
                            mock.Mock(free=60 * GIB), mock.Mock(free=remaining * GIB - 1)]), \
                        mock.patch.object(helper, 'run') as run:
                    with self.assertRaisesRegex(RuntimeError, 'floor was breached during reservation'):
                        helper.create_bounded_storage('fixture', profile, 994)
                    self.assertEqual([call.args[0][0] for call in run.call_args_list], ['fallocate'])
