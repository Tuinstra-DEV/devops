import unittest

import cpu_pools


class CpuPoolTests(unittest.TestCase):
    def test_pool_accounting_is_bounded_to_declared_cpus(self):
        before = {cpu: (100, 60, 0) for cpu in range(16)}
        after = {cpu: (200, 80, 0) for cpu in range(16)}
        after[1] = (200, 150, 0)
        sample = cpu_pools.utilization(before, after, cpu_pools.POOLS["host"])
        self.assertEqual(sample, {"busy_percent": 80.0, "steal_percent": 0.0})
        wow = cpu_pools.utilization(before, after, cpu_pools.POOLS["wow"])
        self.assertLess(wow["busy_percent"], sample["busy_percent"])

    def test_steal_is_reported_separately_from_busy(self):
        before = {0: (100, 20, 0)}
        after = {0: (200, 40, 10)}
        self.assertEqual(cpu_pools.utilization(before, after, (0,)),
                         {"busy_percent": 70.0, "steal_percent": 10.0})

    def test_missing_cpu_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "topology"):
            cpu_pools.utilization({0: (0, 0, 0)}, {0: (1, 0, 0)}, (0, 8))
