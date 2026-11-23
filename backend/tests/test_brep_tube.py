"""Independent analytic geometry checks for native STEP measurements."""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.pp.tube.brep_sections import section_shape, contour_thickness
from core.pp.tube.brep_engine import analyze_brep_tube


from brep_fixtures import tube, cylinder, polygon_tube, elbow, transformed, write_step


class NativeSectionTests(unittest.TestCase):
    def test_circle_perimeter_center_thickness(self):
        shape = tube(25,23,120)
        sec = section_shape(shape, [0, 0, 60], [0, 0, 1])
        self.assertTrue(sec["valid"])
        self.assertAlmostEqual(sec["perimeter"], 2 * np.pi * 25, places=7)
        np.testing.assert_allclose(sec["center"], [0, 0, 60], atol=1e-7)
        self.assertLess(abs(sec["thickness"]["median_mm"] - 2), .004)

    def test_concave_contours_remain_concave(self):
        from shapely.geometry import Polygon
        outer = [(0, 0), (40, 0), (40, 15), (15, 15), (15, 40), (0, 40)]
        inner = [(2, 2), (38, 2), (38, 13), (13, 13), (13, 38), (2, 38)]
        shape = polygon_tube(outer,inner,100)
        sec = section_shape(shape, [0, 0, 50], [0, 0, 1])
        self.assertTrue(sec["valid"])
        self.assertAlmostEqual(sec["perimeter"], 160., places=6)
        np.testing.assert_allclose(sec["center"][:2], np.array(Polygon(outer).centroid.coords)[0], atol=1e-7)
        self.assertLess(Polygon(sec["inner_2d"]).area, Polygon(sec["inner_2d"]).convex_hull.area - 100)
        self.assertAlmostEqual(sec["thickness"]["median_mm"], 2., places=6)

    def test_single_solid_bar_has_no_invented_wall(self):
        shape = cylinder(20,100)
        sec = section_shape(shape, [0, 0, 50], [0, 0, 1])
        self.assertFalse(sec["valid"])
        self.assertEqual(sec["reason"], "not_two_nested_walls")

    def test_eccentric_circle_preserves_wall_range(self):
        sec = section_shape(tube(25,23,100,.8), [0, 0, 50], [0, 0, 1])
        self.assertTrue(sec["valid"])
        self.assertLess(abs(sec["thickness"]["min_mm"] - 1.2), .004)
        self.assertLess(abs(sec["thickness"]["max_mm"] - 2.8), .004)

    def test_rotated_step_and_noninteger_endpoint(self):
        shape = transformed(tube(12,10.5,80.65),37,(123,-94,56))
        with tempfile.TemporaryDirectory() as tmp:
            file = str(Path(tmp) / "rotated.step")
            write_step(shape,file)
            result = analyze_brep_tube(file)
        self.assertLess(abs(result["center_line_length"] - 80.65), .7)
        self.assertEqual(result["center_line_length"], result["sections_full"][-1]["cumulative_length"])
        self.assertLess(abs(result["wall_thickness_stats"]["median_mm"] - 1.5), .004)
        for s in result["sections_full"][::20]:
            p, n = np.array(s["position"]), np.array(s["normal"])
            outline = np.array(s["outline_3d"])
            np.testing.assert_allclose((outline - p) @ n, 0., atol=1e-7)
            rebuilt = p + np.array(s["outer_2d"]) @ np.array([s["basis_u"], s["basis_v"]])
            np.testing.assert_allclose(outline, rebuilt, atol=1e-7)

    def test_circular_elbow_centerline(self):
        radius, angle = 120., 45.
        shape = elbow(radius,10,8.5,angle)
        with tempfile.TemporaryDirectory() as tmp:
            file = str(Path(tmp) / "elbow.step")
            write_step(shape,file)
            result = analyze_brep_tube(file)
        pts = np.array(result["center_line_points"])
        radial_error = abs(np.linalg.norm(pts[:, :2], axis=1) - radius)
        self.assertLess(radial_error.max(), .05)
        self.assertLess(abs(result["center_line_length"] - radius * np.deg2rad(angle)), 1.)
        self.assertLess(abs(result["wall_thickness_stats"]["median_mm"] - 1.5), .006)

    def test_trimmed_end_failures_are_not_internal_missing_sections(self):
        from core.pp.tube.brep_engine import finalize_section_quality
        rows = [dict(position=[float(x), 0, 0], normal=[1, 0, 0],
                     wall_thickness=2., wall_thickness_min=2., centroid_residual_mm=0.,
                     measurement_status='needs_review', normal_deviation_deg=8.)
                for x in range(10, 31)]
        failures = [dict(position=[x, 0, 0], reason='not_two_nested_walls') for x in (8, 9, 21.5, 32)]
        quality, internal, excluded = finalize_section_quality(rows, failures, [1, 0, 0])
        self.assertEqual([r['position'][0] for r in internal], [21.5])
        self.assertEqual([r['position'][0] for r in excluded], [8, 9, 32])
        self.assertLess(quality['max_normal_deviation_deg'], 1e-5)
        self.assertTrue(all(r['measurement_status'] == 'measured' for r in rows))

    def test_post_trim_quality_still_rejects_internal_bad_measurements(self):
        from core.pp.tube.brep_engine import finalize_section_quality
        rows = [dict(position=[float(x), 0, 0], normal=[1, 0, 0],
                     wall_thickness=2., wall_thickness_min=2., centroid_residual_mm=0.)
                for x in range(30)]
        rows[10]['normal'] = [0, 1, 0]
        rows[15]['wall_thickness_min'] = .01
        rows[20]['centroid_residual_mm'] = .3
        finalize_section_quality(rows, [], [1, 0, 0])
        self.assertEqual([i for i, row in enumerate(rows) if row['measurement_status'] == 'needs_review'], [10, 15, 20])


if __name__ == "__main__":
    unittest.main(verbosity=2)
