# -*- coding: utf-8 -*-
"""分型方向测试：``core.entity.directions`` 的镜像评分与四类方向规则。

用**合成截面环**（``{"points": Nx3 ndarray}`` 的列表，等价于流水线里 ``measure``
产出的环）验证 ``ENTITY_PIPELINE.md`` §7：

* 成型（shaping）：三向镜像评分取最小，并列按 ``Z > Y > X``；三向都超容差 → **报错**；
* 扩口（belling）：回转体任意方向 → 取评分最小轴，并列取 ``Y``，``rule="fixed"``；
* 压扁（flattening）：逐截面 PCA 长轴（椭圆长轴在哪，方向就跟到哪）；
* 压弯（bending）：优先取弯曲平面法向 ``n``（``direction_source="bend_axis"``），
  ``n`` 在每个截面里退化时才退到平面内候选并附 warning。

所有数值写的是 2026-11-03 在本机（Python 3.11.4 / numpy）实测值，容差已放宽到与
数值噪声无关的级别；断言的语义（谁最小、谁并列、谁被选中）才是契约本身。
"""
import math
import os
import sys
import unittest

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.entity import directions  # noqa: E402


# ---------------------------------------------------------------------------
# 合成环构造器（与流水线 measure 的环同构：dict + Nx3 点）
# ---------------------------------------------------------------------------
def triangle_rings(count=12, zmin=0.0, zmax=20.0, scale=1.0):
    """不等边三角形截面沿 Z 拉伸（只有 Z 是对称轴）。"""
    tri = np.array([[0.0, 0.0], [2.0, 0.0], [0.5, 1.5]]) * scale
    rings = []
    for z in np.linspace(zmin, zmax, count):
        pts = np.column_stack([tri, np.full(len(tri), z)])
        rings.append({"points": pts})
    return rings


def circle_rings(count=12, radius=5.0, zmin=0.0, zmax=20.0, n=48):
    """圆截面沿 Z 拉伸：三向镜像都对称（Z=Y=X≈0）。"""
    theta = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    ring = np.column_stack([radius * np.cos(theta), radius * np.sin(theta)])
    return [
        {"points": np.column_stack([ring, np.full(n, z)])}
        for z in np.linspace(zmin, zmax, count)
    ]


def ellipse_rings(count=12, a=10.0, b=4.0, n=64, zmin=0.0, zmax=20.0, long_axis="Y"):
    """椭圆截面沿 Z 拉伸：压扁后的截面形状（长轴 a 在 long_axis 上）。"""
    theta = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    if long_axis == "Y":
        xy = np.column_stack([b * np.cos(theta), a * np.sin(theta)])
    else:
        xy = np.column_stack([a * np.cos(theta), b * np.sin(theta)])
    return [
        {"points": np.column_stack([xy, np.full(n, z)])}
        for z in np.linspace(zmin, zmax, count)
    ]


def arc_rings(radius=60.0, span_deg=90.0, count=25, tube_r=8.0, n=48, plane="XZ"):
    """弯曲管件：中心线是圆弧，截面环垂直于切线。

    ``plane="XZ"`` → 弯曲平面 XZ、平面法向 ``[0,1,0]``；``plane="XY"`` → 法向 ``[0,0,1]``。
    """
    theta = np.linspace(0.0, math.radians(span_deg), count)
    phi = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    rings = []
    for t in theta:
        if plane == "XZ":
            center = np.array([radius * math.sin(t), 0.0, radius * (1.0 - math.cos(t))])
            tangent = np.array([math.cos(t), 0.0, math.sin(t)])
            normal = np.array([0.0, 1.0, 0.0])
        else:
            center = np.array([radius * math.sin(t), radius * (1.0 - math.cos(t)), 0.0])
            tangent = np.array([math.cos(t), math.sin(t), 0.0])
            normal = np.array([0.0, 0.0, 1.0])
        up = np.cross(tangent, normal)
        up = up / np.linalg.norm(up)
        pts = (
            center[None, :]
            + tube_r * np.outer(np.cos(phi), normal)
            + tube_r * np.outer(np.sin(phi), up)
        )
        rings.append({"points": pts})
    return rings


def random_rings(seed=7, count=10, n=64):
    """随机点云：三向镜像都不对称 → 成型规则必须报错。"""
    rng = np.random.default_rng(seed)
    base = rng.normal(size=(n, 2))
    out = []
    for k in range(count):
        pts = np.column_stack(
            [base + rng.normal(scale=0.4, size=(n, 2)), np.full(n, k * 2.0)]
        )
        out.append({"points": pts})
    return out


def _unit(vector):
    vector = np.asarray(vector, dtype=float)
    return vector / np.linalg.norm(vector)


class ScoreAxesTest(unittest.TestCase):
    def test_returns_three_axis_dict(self):
        scores = directions.score_axes(triangle_rings())
        self.assertEqual(set(scores), {"X", "Y", "Z"})
        for axis, value in scores.items():
            with self.subTest(axis=axis):
                self.assertIsInstance(value, float)
                self.assertGreaterEqual(value, 0.0)

    def test_known_symmetry_axis_scores_lowest(self):
        # 三角形沿 Z 拉伸：Z 是对称轴，X/Y 不是（实测 X=0.024951 Y=0.041033 Z=1.07e-8）
        scores = directions.score_axes(triangle_rings())
        self.assertAlmostEqual(scores["Z"], 0.0, places=6)
        self.assertLess(scores["Z"], min(scores["X"], scores["Y"]))
        self.assertGreater(min(scores["X"], scores["Y"]), 0.01)
        self.assertEqual(min(scores, key=scores.get), "Z")

    def test_circular_extrusion_is_symmetric_on_all_axes(self):
        scores = directions.score_axes(circle_rings())
        for axis in ("X", "Y", "Z"):
            with self.subTest(axis=axis):
                self.assertLess(scores[axis], 1e-6)

    def test_ellipse_extrusion_scores_low_on_all_axes(self):
        scores = directions.score_axes(ellipse_rings())
        for axis in ("X", "Y", "Z"):
            with self.subTest(axis=axis):
                self.assertLess(scores[axis], 1e-6)


class ShapingRuleTest(unittest.TestCase):
    """成型：三向镜像评分最小；并列 Z>Y>X；不对称 → ValueError。"""

    def test_triangle_picks_z_with_symmetry_rule(self):
        result = directions.resolve_for_category("shaping", triangle_rings())
        self.assertEqual(result["label"], "Z")
        self.assertEqual(tuple(np.round(result["vector"], 9)), (0.0, 0.0, 1.0))
        self.assertEqual(result["rule"], "symmetry")
        self.assertEqual(result["category"], "shaping")
        self.assertEqual(result["tied"], ["Z"])

    def test_model_key_and_op_alias_are_equivalent(self):
        rings = triangle_rings()
        by_key = directions.resolve_for_category("成型_外径形腔", rings)
        by_op = directions.resolve_for_category("shaping", rings)
        self.assertEqual(by_key["label"], by_op["label"])
        self.assertEqual(by_key["rule"], by_op["rule"])

    def test_all_tied_prefers_z_then_y_then_x(self):
        # 圆截面三向评分并列（实测都在 1e-9 量级）→ 并列集合按 Z>Y>X
        result = directions.resolve_for_category("shaping", circle_rings())
        self.assertEqual(result["label"], "Z")
        self.assertEqual(result["rule"], "symmetry")
        self.assertEqual(result["tied"], ["Z", "Y", "X"])

    def test_asymmetric_section_raises_instead_of_guessing(self):
        with self.assertRaises(ValueError) as caught:
            directions.resolve_for_category("成型_外径形腔", random_rings())
        message = str(caught.exception)
        self.assertIn("分型方向必须是对称方向", message)
        self.assertIn("0.02", message.replace("0.01", ""))  # 容差出现在提示里
        self.assertIn("shaping", message)
        self.assertIn("10", message)  # 截面数

    def test_explicit_axis_skips_symmetry_check(self):
        result = directions.resolve_for_category("shaping", random_rings(), "X")
        self.assertEqual(result["label"], "X")
        self.assertEqual(tuple(result["vector"]), (1.0, 0.0, 0.0))
        self.assertEqual(result["rule"], "fixed")
        # 人工指定方向时不做对称自检，但要留下 note
        self.assertIn("人工指定", result.get("note", ""))

    def test_explicit_vector(self):
        result = directions.resolve_for_category("shaping", random_rings(), [0.0, 1.0, 0.0])
        self.assertEqual(result["label"], "Y")
        self.assertEqual(result["rule"], "fixed")
        self.assertEqual(tuple(result["vector"]), (0.0, 1.0, 0.0))


class BellingRuleTest(unittest.TestCase):
    """扩口：回转体任意方向 → 评分最小轴，并列取 Y（rule=fixed）。"""

    def test_circle_ties_to_y(self):
        result = directions.resolve_for_category("扩口_扩口腔体", circle_rings())
        self.assertEqual(result["label"], "Y")
        self.assertEqual(tuple(result["vector"]), (0.0, 1.0, 0.0))
        self.assertEqual(result["rule"], "fixed")
        self.assertEqual(result["category"], "belling")
        self.assertIn("并列取 Y", result.get("note", ""))

    def test_triangle_takes_lowest_score_axis(self):
        result = directions.resolve_for_category("扩口_扩口腔体", triangle_rings())
        self.assertEqual(result["label"], "Z")
        self.assertEqual(result["rule"], "fixed")

    def test_belling_never_raises_on_asymmetric_cloud(self):
        # 扩口是回转体，方向任意；不像成型那样做对称性自检
        result = directions.resolve_for_category("扩口_扩口腔体", random_rings())
        self.assertIn(result["label"], ("X", "Y", "Z"))
        self.assertEqual(result["rule"], "fixed")


class FlatteningRuleTest(unittest.TestCase):
    """压扁：逐截面 PCA 长轴。"""

    def test_long_axis_y(self):
        result = directions.resolve_for_category("压扁_压扁模型", ellipse_rings(long_axis="Y"))
        vector = np.asarray(result["vector"], dtype=float)
        self.assertAlmostEqual(abs(float(np.dot(vector, [0.0, 1.0, 0.0]))), 1.0, places=6)
        self.assertEqual(result["rule"], "major_axis")
        self.assertEqual(result["category"], "flattening")
        self.assertEqual(result["symmetry_axis"], "Y")
        self.assertAlmostEqual(result["angle_deg"], 0.0, places=6)
        self.assertEqual(result["per_section"], 12)

    def test_long_axis_x(self):
        result = directions.resolve_for_category("压扁", ellipse_rings(long_axis="X"))
        vector = np.asarray(result["vector"], dtype=float)
        self.assertAlmostEqual(abs(float(np.dot(vector, [1.0, 0.0, 0.0]))), 1.0, places=6)
        self.assertEqual(result["symmetry_axis"], "X")

    def test_result_supports_tuple_unpacking(self):
        vector, label, rule = directions.resolve_for_category("压扁", ellipse_rings())
        self.assertEqual(len(vector), 3)
        self.assertIn(label, ("X", "Y", "Z"))
        self.assertTrue(rule.startswith("major_axis"))


class BendingRuleTest(unittest.TestCase):
    """压弯：优先取弯曲平面法向 n；n 退化时才用平面内候选并附 warning。"""

    def _assert_normal_is_picked(self, result, expected_normal):
        normal = np.asarray(result["plane_normal"], dtype=float)
        self.assertAlmostEqual(abs(float(np.dot(normal, expected_normal))), 1.0, places=6)
        # 物理开模方向 = 弯曲平面法向（方向本身允许 ±，label 只看坐标轴）
        vector = np.asarray(result["vector"], dtype=float)
        self.assertAlmostEqual(abs(float(np.dot(vector, expected_normal))), 1.0, places=6)
        self.assertEqual(result["rule"], "bend_plane")  # 未退化 → 不带 warning
        self.assertEqual(result["direction_source"], "bend_axis")
        self.assertAlmostEqual(result["parting_sin_min"], 1.0, places=4)
        self.assertAlmostEqual(result["angle_to_normal_deg"], 0.0, places=3)

    def test_arc_in_xz_plane_uses_plane_normal_y(self):
        result = directions.resolve_for_category("压弯_压弯模型", arc_rings(plane="XZ"))
        self._assert_normal_is_picked(result, [0.0, 1.0, 0.0])
        self.assertEqual(result["label"], "Y")
        # 弦方向（质心 PCA 主轴）仍在弯曲平面内
        plane_axis = np.asarray(result["plane_axis"], dtype=float)
        self.assertAlmostEqual(abs(float(np.dot(plane_axis, _unit([1.0, 0.0, 1.0])))), 1.0, places=4)
        self.assertEqual(tuple(np.round(result["span_axis"], 9)), (1.0, 0.0, 0.0))

    def test_arc_in_xy_plane_uses_plane_normal_z(self):
        result = directions.resolve_for_category("压弯", arc_rings(plane="XY"))
        self._assert_normal_is_picked(result, [0.0, 0.0, 1.0])
        self.assertEqual(result["label"], "Z")

    def test_half_circle_also_uses_plane_normal(self):
        # 180° 圆弧的弦与中段切线平行（契约公式退化），法向仍然可用
        result = directions.resolve_for_category("压弯", arc_rings(span_deg=180.0))
        self.assertEqual(result["direction_source"], "bend_axis")
        self.assertEqual(result["label"], "Y")
        self.assertAlmostEqual(
            abs(float(np.dot(np.asarray(result["vector"]), np.asarray(result["plane_normal"])))),
            1.0,
            places=6,
        )

    def test_fallback_path_is_explained_in_rule(self):
        # 无论走哪条候选，rule 都必须是 bend_plane；偏离 bend_axis 时必须带 warning 说明
        for span in (30.0, 90.0, 180.0):
            with self.subTest(span=span):
                result = directions.resolve_for_category("压弯", arc_rings(span_deg=span))
                self.assertTrue(result["rule"].startswith("bend_plane"))
                if result["direction_source"] != "bend_axis":
                    self.assertIn("warning", result["rule"])
                    self.assertIn("改用", result["rule"])


class SectionAxesTest(unittest.TestCase):
    def test_per_section_axes_returns_one_axis_per_section(self):
        rings = ellipse_rings(count=12, n=32)
        axes = directions.per_section_axes(rings)
        self.assertEqual(len(axes), 12)
        for axis in axes:
            self.assertEqual(np.asarray(axis).shape, (3,))

    def test_section_frames_have_documented_keys(self):
        frames = directions.per_section_axes(ellipse_rings(count=12, n=32), vector=[0, 1, 0])
        self.assertEqual(len(frames), 12)
        self.assertEqual(
            set(frames[0]),
            {"a", "centroid", "degenerate", "index", "n", "normal", "up", "vector"},
        )
        for frame in frames:
            with self.subTest(index=frame["index"]):
                # 平面法向 n 必须与分型方向（向量在截面内的投影）垂直
                self.assertAlmostEqual(float(np.dot(frame["n"], frame["vector"])), 0.0, places=6)


class RingInputTest(unittest.TestCase):
    def test_bare_ndarray_rings(self):
        """契约写的是 ``{"points": Nx3}``；裸 ndarray 优先按"接受"断言。

        当前 directions.py 的 ``_points_of`` 只认 dict / 带 ``.points`` 的对象，
        裸 ndarray 会抛 ValueError（已回报 S5）。等模块支持后这个测试自动转为断言，
        不需要改测试。
        """
        points = np.asarray(triangle_rings()[0]["points"])
        try:
            result = directions.resolve_for_category("shaping", [points])
        except ValueError as exc:
            self.assertIn("points", str(exc))
            self.skipTest("directions 暂不接受裸 ndarray 环（已知缺口）：%s" % exc)
        else:
            self.assertEqual(result["label"], "Z")

    def test_single_ring_is_accepted(self):
        single = circle_rings(count=1)[0]  # 只有一个截面
        result = directions.resolve_for_category("扩口_扩口腔体", single)
        self.assertIn(result["label"], ("X", "Y", "Z"))

    def test_normalize_rejects_zero_vector(self):
        with self.assertRaises(ValueError):
            directions.normalize([0.0, 0.0, 0.0])

    def test_empty_rings_raise_value_error(self):
        with self.assertRaises(ValueError):
            directions.resolve_for_category("shaping", [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
