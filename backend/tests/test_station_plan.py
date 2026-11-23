# -*- coding: utf-8 -*-
"""站位规划：基准步长、曲率加密、1 mm 网格重建（GTAPP_STATION_MM 新链路）。"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pp.tube import station_plan  # noqa: E402


def _row(station, perimeter, wall=2.0):
    """造一个带轮廓的实测站（字段与 brep_engine.section_record 对齐）。"""
    s = float(station)
    return {
        'cumulative_length': s,
        'position': [0.0, 0.0, s], 'normal': [0.0, 0.0, 1.0],
        'basis_u': [1.0, 0.0, 0.0], 'basis_v': [0.0, 1.0, 0.0],
        'perimeter': float(perimeter),
        'inner_perimeter': float(perimeter) - 2.0 * np.pi * float(wall),
        'equivalent_diameter': float(perimeter) / np.pi,
        'wall_thickness': float(wall), 'wall_thickness_min': float(wall) * .9,
        'wall_thickness_max': float(wall) * 1.1, 'wall_thickness_mean': float(wall),
        'wall_area': 0.5, 'centroid_residual_mm': 0.01, 'normal_deviation_deg': .1,
        'measurement_status': 'measured', 'thickness_sample_count': 192,
        'contour_repairs': [], 'plane_offset_mm': 0.0, 'intersection_fuzzy_mm': 0.0,
        'outer_2d': [[1., 0.], [0., 1.], [-1., 0.], [0., -1.]],
        'inner_2d': [[.9, 0.], [0., .9], [-.9, 0.], [0., -.9]],
        'outline_3d': [[1., 0., s], [0., 1., s], [-1., 0., s], [0., -1., s]],
        'inner_outline_3d': [[.9, 0., s], [0., .9, s], [-.9, 0., s], [0., -.9, s]],
    }


class StationStepTest(unittest.TestCase):

    def test_default_is_histogram_one_mm(self):
        env = {k: v for k, v in os.environ.items() if k != 'GTAPP_STATION_MM'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(station_plan.station_step_mm(), 1.0)

    def test_env_value_and_clamp(self):
        with mock.patch.dict(os.environ, {'GTAPP_STATION_MM': '3'}, clear=False):
            self.assertEqual(station_plan.station_step_mm(), 3.0)
        with mock.patch.dict(os.environ, {'GTAPP_STATION_MM': '999'}, clear=False):
            self.assertEqual(station_plan.station_step_mm(), station_plan.MAX_STATION_MM)
        with mock.patch.dict(os.environ, {'GTAPP_STATION_MM': '0.2'}, clear=False):
            self.assertEqual(station_plan.station_step_mm(), 1.0)
        with mock.patch.dict(os.environ, {'GTAPP_STATION_MM': 'abc'}, clear=False):
            self.assertEqual(station_plan.station_step_mm(), 1.0)

    def test_coarse_positions_includes_end(self):
        pos = station_plan.coarse_positions(10.0, 3.0)
        self.assertEqual(pos.tolist(), [0.0, 3.0, 6.0, 9.0, 10.0])


class LocalizationKnobTest(unittest.TestCase):
    """A/B 旋钮的实际默认值由实测决定：粗种子恒 5 mm、3 轮全切、提前收敛关闭。"""

    def _clean(self, **extra):
        drop = ('GTAPP_COARSE_STEP_MM', 'GTAPP_CORRECTION_ROUNDS',
                'GTAPP_CORRECTION_TOL_MM')
        env = {k: v for k, v in os.environ.items() if k not in drop}
        env.update(extra)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_defaults_keep_localization_unchanged(self):
        with self._clean():
            for station in (None, 1.0, 3.0, 10.0):
                self.assertEqual(station_plan.coarse_step_mm(station), 5.0)
                self.assertEqual(station_plan.correction_rounds(station), 3)
                self.assertEqual(station_plan.correction_tolerance_mm(station), 0.0)

    def test_coarse_step_explicit_and_clamped(self):
        with self._clean(GTAPP_COARSE_STEP_MM='12'):
            self.assertEqual(station_plan.coarse_step_mm(), 12.0)
        with self._clean(GTAPP_COARSE_STEP_MM='1'):
            self.assertEqual(station_plan.coarse_step_mm(), station_plan.MIN_COARSE_STEP_MM)
        with self._clean(GTAPP_COARSE_STEP_MM='99'):
            self.assertEqual(station_plan.coarse_step_mm(), station_plan.MAX_COARSE_STEP_MM)
        with self._clean(GTAPP_COARSE_STEP_MM='abc'):
            self.assertEqual(station_plan.coarse_step_mm(), 5.0)

    def test_rounds_and_tolerance(self):
        with self._clean(GTAPP_CORRECTION_ROUNDS='2'):
            self.assertEqual(station_plan.correction_rounds(), 2)
        with self._clean(GTAPP_CORRECTION_ROUNDS='9'):
            self.assertEqual(station_plan.correction_rounds(), 5)
        with self._clean(GTAPP_CORRECTION_ROUNDS='0'):
            self.assertEqual(station_plan.correction_rounds(), 3)
        with self._clean(GTAPP_CORRECTION_TOL_MM='0.05'):
            self.assertAlmostEqual(station_plan.correction_tolerance_mm(), 0.05)
        with self._clean(GTAPP_CORRECTION_TOL_MM='-1'):
            self.assertEqual(station_plan.correction_tolerance_mm(), 0.0)
        with self._clean(GTAPP_CORRECTION_TOL_MM='9'):
            self.assertEqual(station_plan.correction_tolerance_mm(), 2.0)


class StationGridTest(unittest.TestCase):
    """归档网格（GTAPP_STATION_GRID）与实测步长解耦。"""

    def test_default_grid_is_one_mm(self):
        env = {k: v for k, v in os.environ.items() if k != 'GTAPP_STATION_GRID'}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(station_plan.station_grid_mm(), 1.0)

    def test_grid_env_value_and_clamp(self):
        with mock.patch.dict(os.environ, {'GTAPP_STATION_GRID': '3'}, clear=False):
            self.assertEqual(station_plan.station_grid_mm(), 3.0)
        with mock.patch.dict(os.environ, {'GTAPP_STATION_GRID': '99'}, clear=False):
            self.assertEqual(station_plan.station_grid_mm(), station_plan.MAX_GRID_STEP_MM)
        with mock.patch.dict(os.environ, {'GTAPP_STATION_GRID': '0.5'}, clear=False):
            self.assertEqual(station_plan.station_grid_mm(), 1.0)
        with mock.patch.dict(os.environ, {'GTAPP_STATION_GRID': 'x'}, clear=False):
            self.assertEqual(station_plan.station_grid_mm(), 1.0)

    def test_three_mm_archive_is_decimated_not_resampled(self):
        # 归档只做抽稀：3 mm 归档 = 1 mm 行的每 3 行取 1 行（保留末行），不再插值，
        # 避免二次插值误差。
        rows = [_row(float(s), 100.0 + .5 * s) for s in range(13)]
        out, info = station_plan.decimate_rows(rows, grid_step=3.0)
        self.assertEqual([r['cumulative_length'] for r in out],
                         [0.0, 3.0, 6.0, 9.0, 12.0])
        self.assertEqual(info, {'archive_step_mm': 3.0, 'archive_rows': 5})
        same, info1 = station_plan.decimate_rows(rows, grid_step=1.0)
        self.assertEqual(len(same), 13)
        self.assertEqual(info1, {'archive_step_mm': 1.0, 'archive_rows': 13})

    def test_three_mm_grid_expands_dense_rows_first(self):
        rows = [_row(s, 100.0 + .5 * s) for s in (0.0, 3.0, 6.0, 9.0, 12.0)]
        dense, info = station_plan.expand_rows(rows, grid_step=1.0)
        self.assertEqual(len(dense), 13)
        self.assertEqual(info['interpolated'], 8)
        archived, _ = station_plan.decimate_rows(dense, grid_step=3.0)
        self.assertEqual(len(archived), 5)


class SectionRecordsTest(unittest.TestCase):
    """计算层记录始终是 1 mm：归档网格粗时必须先插值回来。"""

    def test_coarse_archive_is_densified_for_process_records(self):
        from core.pp.section_records import records_from_sections
        rows = [_row(s, 100.0 + .5 * s) for s in (0.0, 3.0, 6.0, 9.0, 12.0)]
        recs = records_from_sections(rows)
        self.assertEqual(len(recs), 13)
        mid = recs[4]
        self.assertEqual(mid['中心线长度(mm)'], 4.0)
        self.assertEqual(mid['station_source'], 'interpolated')
        self.assertAlmostEqual(mid['外径周长(mm)'], 102.0, places=6)

    def test_one_mm_rows_are_untouched(self):
        from core.pp.section_records import records_from_sections
        rows = [_row(float(s), 100.0) for s in range(6)]
        recs = records_from_sections(rows)
        self.assertEqual(len(recs), 6)
        self.assertTrue(all(r['station_source'] == 'measured' for r in recs))


class RefinePositionsTest(unittest.TestCase):

    def test_linear_cone_needs_no_refinement(self):
        s = np.arange(0.0, 12.0 + 1e-9, 3.0)
        planned = station_plan.refine_positions(s, 100.0 + .5 * s, np.full(s.size, 2.0),
                                                step_mm=3.0, end_fine_mm=0.0)
        self.assertEqual(planned.size, 0)

    def test_bend_refines_only_around_bend(self):
        s = np.array([0.0, 3.0, 6.0, 9.0, 12.0])
        p = np.array([10.0, 10.0, 14.0, 10.0, 10.0])
        planned = station_plan.refine_positions(s, p, np.full(s.size, 2.0),
                                                step_mm=3.0, end_fine_mm=0.0)
        # 只有弯所在的 0-12 mm 内部被补成 1 mm，且不含已测站位
        self.assertTrue(planned.size > 0)
        for measured in s:
            self.assertNotIn(round(float(measured), 6), np.round(planned, 6).tolist())
        self.assertLessEqual(float(planned.max()), 12.0)

    def test_ends_forced_to_one_mm(self):
        s = np.arange(0.0, 60.0 + 1e-9, 3.0)
        planned = station_plan.refine_positions(s, 100.0 + 0.0 * s, np.full(s.size, 2.0),
                                                step_mm=3.0, end_fine_mm=15.0)
        self.assertIn(1.0, np.round(planned, 6).tolist())
        self.assertIn(59.0, np.round(planned, 6).tolist())

    def test_no_plan_at_one_mm(self):
        s = np.arange(0.0, 30.0, 1.0)
        planned = station_plan.refine_positions(s, 100.0 + s, np.full(s.size, 2.0), step_mm=1.0)
        self.assertEqual(planned.size, 0)


class ExpandRowsTest(unittest.TestCase):

    def test_linear_scalars_round_trip(self):
        rows = [_row(s, 100.0 + .5 * s) for s in (0.0, 3.0, 6.0, 9.0, 12.0)]
        out, info = station_plan.expand_rows(rows)
        self.assertEqual(len(out), 13)
        self.assertEqual(info['interpolated'], 8)
        self.assertEqual(info['sources'], {'measured': 5, 'interpolated': 8})
        for row in out:
            s = row['cumulative_length']
            self.assertAlmostEqual(row['perimeter'], 100.0 + .5 * s, places=6)
            self.assertAlmostEqual(row['wall_thickness'], 2.0, places=6)
        self.assertEqual([r['index'] for r in out], list(range(13)))

    def test_interpolated_rows_reuse_nearest_contour(self):
        rows = [_row(s, 100.0) for s in (0.0, 3.0, 6.0)]
        out, _ = station_plan.expand_rows(rows)
        row = next(r for r in out if abs(r['cumulative_length'] - 1.0) < 1e-9)
        self.assertEqual(row['measurement_status'], 'interpolated')
        self.assertEqual(row['station_source'], 'interpolated')
        self.assertEqual(row['contour_inherited_from_mm'], 0.0)
        self.assertIsNotNone(row['outer_2d'])
        # 轮廓沿法向平移到本站位，不能把邻站的 3D 轮廓原样搬过来
        self.assertAlmostEqual(row['outline_3d'][0][2], 1.0, places=6)
        self.assertEqual(row['thickness_sample_count'], 0)

    def test_shifted_axis_snaps_to_measured_phase(self):
        # 真实数据的两段 s 轴：端部外推段是整数站（0,1,2），实测段带 .37 相位
        # （端部修正整体平移造成）。网格必须按实测相位铺，实测行原样保留、不重复。
        rows = [_row(s, 100.0) for s in (0.0, 1.0, 2.0)]
        for row in rows:
            row['measurement_status'] = 'extrapolated'
        rows += [_row(s, 100.0 + .5 * (s - 2.37)) for s in (2.37, 5.37, 8.37)]
        out, info = station_plan.expand_rows(rows)
        s_list = [round(r['cumulative_length'], 6) for r in out]
        self.assertEqual(s_list, [0.0, 1.0, 2.0, 2.37, 3.37, 4.37, 5.37, 6.37, 7.37, 8.37])
        self.assertEqual(info['sources'], {'extrapolated': 3, 'measured': 3, 'interpolated': 4})
        for row in out:
            s = row['cumulative_length']
            if row['station_source'] in ('measured', 'extrapolated'):
                expect = 100.0 + .5 * (s - 2.37) if s >= 2.37 else 100.0
                self.assertAlmostEqual(row['perimeter'], expect, places=6)
            else:  # 插值站夹在相邻实测值之间（2.37 处有折点，不是纯线性）
                self.assertGreaterEqual(row['perimeter'], 100.0 - 1e-9)
                self.assertLessEqual(row['perimeter'], 103.0 + 1e-9)
        self.assertTrue(all(np.isfinite(r['perimeter']) for r in out))

    def test_near_duplicate_rows_are_merged(self):
        rows = [_row(s, 100.0) for s in (0.0, 10.0)]
        rows.insert(1, {**_row(10.0346, 999.0), 'station_source': 'recovered'})
        out, info = station_plan.expand_rows(rows)
        s_list = [round(r['cumulative_length'], 6) for r in out]
        self.assertEqual(len(s_list), len(set(s_list)))  # 不允许重复站位
        near = [r for r in out if abs(r['cumulative_length'] - 10.0) < 1e-6]
        self.assertEqual(len(near), 1)  # 0.0346 mm 的两行只留一行，不产生双份读数
        self.assertNotIn(999.0, [r['perimeter'] for r in near])

    def test_extrapolated_rows_are_preserved(self):
        rows = [_row(s, 100.0) for s in (0.0, 3.0, 6.0)]
        rows.append({**_row(7.0, 101.0), 'measurement_status': 'extrapolated'})
        out, info = station_plan.expand_rows(rows)
        tail = next(r for r in out if abs(r['cumulative_length'] - 7.0) < 1e-9)
        self.assertEqual(tail['station_source'], 'extrapolated')
        self.assertEqual(tail['measurement_status'], 'extrapolated')
        self.assertEqual(info['sources'].get('extrapolated'), 1)


if __name__ == '__main__':
    unittest.main()
