# -*- coding: utf-8 -*-
"""站内并行进程数的选择规则（默认受物理核与可用内存共同限制）。"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pp.tube import section_pool  # noqa: E402


class MeasurementWorkersTest(unittest.TestCase):

    def test_env_wins(self):
        with mock.patch.dict(os.environ, {section_pool.WORKERS_ENV: '3'}, clear=False):
            with mock.patch.object(section_pool, 'physical_cores', return_value=16), \
                    mock.patch.object(section_pool, 'available_commit_mb', return_value=4096):
                self.assertEqual(section_pool.measurement_workers(), 3)

    def test_env_is_clamped(self):
        with mock.patch.dict(os.environ, {section_pool.WORKERS_ENV: '999'}, clear=False):
            self.assertEqual(section_pool.measurement_workers(), section_pool.MAX_WORKERS)
        with mock.patch.dict(os.environ, {section_pool.WORKERS_ENV: '0'}, clear=False):
            self.assertEqual(section_pool.measurement_workers(), 1)

    def test_parallel_switch_disables(self):
        with mock.patch.dict(os.environ, {section_pool.PARALLEL_ENV: '0'}, clear=False):
            with mock.patch.object(section_pool, 'physical_cores', return_value=16):
                self.assertEqual(section_pool.measurement_workers(), 1)

    def test_default_capped_by_eight(self):
        """14 物理核 + 充足内存也只给 8，避免默认开太猛。"""
        env = {k: v for k, v in os.environ.items() if k != section_pool.WORKERS_ENV}
        env.pop(section_pool.PARALLEL_ENV, None)
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(section_pool, 'physical_cores', return_value=14), \
                    mock.patch.object(section_pool, 'available_commit_mb', return_value=16000):
                self.assertEqual(section_pool.measurement_workers(), 8)

    def test_default_capped_by_memory(self):
        """16 GB 机器可用提交只有 4 GB 时压到 1，避免子进程整批饿死。"""
        env = {k: v for k, v in os.environ.items() if k != section_pool.WORKERS_ENV}
        env.pop(section_pool.PARALLEL_ENV, None)
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(section_pool, 'physical_cores', return_value=14), \
                    mock.patch.object(section_pool, 'available_commit_mb', return_value=4000):
                self.assertEqual(section_pool.measurement_workers(), 1)

    def test_memory_limit_keeps_small_core_count(self):
        with mock.patch.object(section_pool, 'available_commit_mb', return_value=12000):
            self.assertEqual(section_pool.memory_limited_workers(6), 6)

    def test_memory_unknown_means_no_limit(self):
        with mock.patch.object(section_pool, 'available_commit_mb', return_value=None):
            self.assertEqual(section_pool.memory_limited_workers(14), 14)


if __name__ == '__main__':
    unittest.main()
