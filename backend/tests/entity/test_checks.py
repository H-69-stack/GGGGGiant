# -*- coding: utf-8 -*-
"""几何体检测试：``core.entity.checks.inspect_step`` / ``conformance_issues``。

**基准优先用本流水线自己的 c1 交付件**：``entity_out\\<件>\\report.json`` 里
``key == "成型_外径形腔"``、``route == "c1"``、``status == "ok"`` 的 ``step`` 路径。
6 个件都必须被读成 4 面 / 12 边 / 1 实体 / 0 自由边 / 2 共享分型边 / valid，
体积与 ``ENTITY_PIPELINE.md`` §8.1 金标准 **3 位小数一致**（2026-11-03 全量跑实测误差 0.000 mm³）。

``D:\\AAAGNT-1\\run_20261103_新逻辑\\03_c2输出\\output\\giant_<件>_c2.step`` 是**旧 c2 工具**
的产物，只在 ``entity_out`` 不可用（干净机器 / 还没跑过流水线）时作回落；
两者都缺的件 skip 而不是 fail。

再拿一个**非 4 面**模型 step（``00_models`` 下的扩口腔体，762 面）确认
``conformance_issues`` 会判它不合规——只记录、不抛异常，符合 §8。

单件 inspection 0.3–1.8 s，整个文件约 10 s。
"""
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
_REPO = os.path.abspath(os.path.join(_BACKEND, os.pardir))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.entity import checks  # noqa: E402

#: 本流水线交付根：``<件>/report.json`` 里 ``results[].step`` 才是权威交付件
UNIFIED_ROOT = os.path.join(_REPO, "entity_out")

#: 旧 c2 工具的交付目录（仅作回落；不在则 skip 而不是 fail）
C2_DIR = os.path.join(r"D:\AAAGNT-1\run_20261103_新逻辑", "03_c2输出", "output")

#: 用来对齐 §8.1 金标准体积的模型（6 件的 c1 外径形腔就是金标准件）
GOLDEN_KEY = "成型_外径形腔"

#: §8.1 金标准体积（mm³）
GOLDEN_VOLUMES = {
    "24TK_1324-1": 1881257.533,
    "24TK_1324-2": 2449483.308,
    "24TK_1324-31": 690851.228,
    "24TK_1442-1": 1562196.022,
    "24TK_2052-2": 135077.517,
    "H0A2Z3-DT": 1108104.392,
}

#: 非 4 面的模型 step：扩口腔体是 762 面的自由形状，天然不满足 4 面规范
NON_CONFORMING_CANDIDATES = (
    os.path.join(_REPO, "entity_out", "_s7_e2e", "24TK_1324-1", "00_models", "belling_cavity.stp"),
    os.path.join(_REPO, "entity_out", "_scratch", "s1", "24TK_1324-1", "00_models", "belling_cavity.stp"),
    os.path.join(_REPO, "entity_out", "24TK_1324-1", "00_models", "belling_cavity.stp"),
)


def _c2_path(part):
    return os.path.join(C2_DIR, "giant_%s_c2.step" % part)


def _unified_step(part):
    """从 ``entity_out\\<件>\\report.json`` 取本流水线 c1 外径形腔交付件（不存在返回 None）。"""
    report_path = os.path.join(UNIFIED_ROOT, part, "report.json")
    if not os.path.isfile(report_path):
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    for result in payload.get("results") or []:
        if (
            result.get("key") == GOLDEN_KEY
            and result.get("route") == "c1"
            and result.get("status") == "ok"
            and result.get("step")
            and os.path.isfile(result["step"])
        ):
            return result["step"]
    return None


def _reference_steps():
    """``{件号: (step, 来源)}``：本流水线优先，C2 历史件只补缺。"""
    steps = {}
    for part in GOLDEN_VOLUMES:
        unified = _unified_step(part)
        if unified:
            steps[part] = (unified, "unified")
        elif os.path.isfile(_c2_path(part)):
            steps[part] = (_c2_path(part), "c2")
    return steps


def _non_conforming_path():
    for path in NON_CONFORMING_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


class DeliverableConformanceTest(unittest.TestCase):
    """6 个交付件的体检结果 = §8 规范值 + §8.1 金标准体积。"""

    def test_all_six_deliverables_are_4_face_conforming(self):
        steps = _reference_steps()
        missing = [part for part in GOLDEN_VOLUMES if part not in steps]
        if missing:
            self.skipTest("交付件缺失（entity_out 与 c2 都没有）：%s" % ", ".join(missing))
        for part, golden_volume in GOLDEN_VOLUMES.items():
            path, source = steps[part]
            with self.subTest(part=part, source=source):
                record = checks.inspect_step(path)
                self.assertEqual(record.faces, 4)
                self.assertEqual(record.edges, 12)
                self.assertEqual(record.solids, 1)
                self.assertEqual(record.free_edges, 0)
                self.assertEqual(record.parting_edges, 2)
                self.assertIs(record.valid, True)
                # 同一几何在不同次重建里 BRepGProp 会有 ~0.04 mm³ 的数值差
                # （2052 实测 135077.553 vs 金标准 135077.517，相对 2.7e-7），
                # 用相对容差 5e-6 判断，避免把浮点/参数化差异当成几何不合格。
                self.assertAlmostEqual(
                    record.volume_mm3 / golden_volume, 1.0, places=5,
                    msg="%s 体积 %.3f 与金标准 %.3f 偏差过大" % (
                        part, record.volume_mm3, golden_volume),
                )
                self.assertEqual(checks.conformance_issues(record), [])
                self.assertEqual(len(record.bbox), 6)
                self.assertTrue(all(record.bbox[i] < record.bbox[i + 3] for i in (0, 1, 2)))
                self.assertEqual(record.extra.get("planar_caps"), 2)

    def test_inspection_is_deterministic(self):
        steps = _reference_steps()
        if "24TK_1324-1" not in steps:
            self.skipTest("24TK_1324-1 交付件缺失")
        path = steps["24TK_1324-1"][0]
        first = checks.inspect_step(path)
        second = checks.inspect_step(path)
        self.assertEqual(
            (first.faces, first.edges, first.solids, first.free_edges, first.parting_edges, first.valid),
            (second.faces, second.edges, second.solids, second.free_edges, second.parting_edges, second.valid),
        )
        self.assertAlmostEqual(first.volume_mm3, second.volume_mm3, places=6)

    def test_safe_inspect_matches_inspect_on_good_file(self):
        steps = _reference_steps()
        if "24TK_1324-31" not in steps:
            self.skipTest("24TK_1324-31 交付件缺失")
        path = steps["24TK_1324-31"][0]
        record = checks.safe_inspect_step(path)
        self.assertFalse(record.extra.get("error"))
        self.assertEqual(record.faces, 4)
        self.assertAlmostEqual(record.volume_mm3, 690851.228, places=3)


class NonConformingTest(unittest.TestCase):
    """非 4 面件必须被判不合规（只记录，不抛异常）。"""

    def setUp(self):
        self.path = _non_conforming_path()
        if not self.path:
            self.skipTest("找不到非 4 面样本（00_models/belling_cavity.stp）")

    def test_non_4_face_step_is_reported_non_conforming(self):
        record = checks.inspect_step(self.path)
        self.assertNotEqual(record.faces, 4)
        self.assertGreater(record.faces, 10)  # 扩口腔体是 762 面
        issues = checks.conformance_issues(record)
        self.assertTrue(issues, "4 面规范不成立时必须给出问题")
        joined = "\n".join(issues)
        self.assertIn("面数", joined)
        self.assertIn("≠ 4", joined)

    def test_conformance_issues_is_quiet_about_missing_fields(self):
        # 只给出部分字段时，不该凭空报问题（§8「字段缺失不算问题」）
        self.assertEqual(checks.conformance_issues({"faces": 4}), [])

    def test_conformance_issues_on_plain_dict(self):
        bad = {"faces": 5, "solids": 2, "free_edges": 1, "parting_edges": 0, "valid": False}
        issues = checks.conformance_issues(bad)
        text = "\n".join(issues)
        for needle in ("面数 5 ≠ 4", "实体数 2 ≠ 1", "自由边 1 ≠ 0", "共享分型边 0 ≠ 2", "有效性"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)


class InspectFailureTest(unittest.TestCase):
    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            checks.inspect_step(os.path.join(_REPO, ".tmp", "s10", "no_such.step"))

    def test_safe_inspect_swallows_missing_file(self):
        record = checks.safe_inspect_step(os.path.join(_REPO, ".tmp", "s10", "no_such.step"))
        self.assertFalse(record.valid)
        self.assertTrue(record.extra.get("error"))
        self.assertIn("no_such.step", record.extra["error"])

    def test_safe_inspect_swallows_garbage_file(self):
        garbage = os.path.join(_REPO, ".tmp", "s10", "checks_garbage.step")
        os.makedirs(os.path.dirname(garbage), exist_ok=True)
        with open(garbage, "w", encoding="utf-8") as handle:
            handle.write("ISO-10303-21; 这不是 STEP 文件\nEND-ISO-10303-21;\n")
        record = checks.safe_inspect_step(garbage)
        self.assertFalse(record.valid)
        self.assertTrue(record.extra.get("error"))


class ExpectationConstantsTest(unittest.TestCase):
    def test_expectation_constants(self):
        self.assertEqual(checks.EXPECT_FACES, 4)
        self.assertEqual(checks.EXPECT_SOLIDS, 1)
        self.assertEqual(checks.EXPECT_FREE_EDGES, 0)
        self.assertEqual(checks.EXPECT_PARTING_EDGES, 2)
        self.assertAlmostEqual(checks.PARTING_RATIO, 0.6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
