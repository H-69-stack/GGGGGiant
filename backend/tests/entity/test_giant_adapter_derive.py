# -*- coding: utf-8 -*-
"""端部口径派生测试：``giant_adapter.derive_analysis`` 不重新测量也能换口径。

c1 与 v1 共用同一批实测截面与站点网格，差异只在 ``complete_ends`` 的
``shape_extrapolation`` 分支。这里用合成的直管截面验证三件事：

* 派生结果与「直接对同一批截面跑一次 v1 端部处理」逐站一致；
* 中间实测站位原样保留，只有两端外推站位被重算；
* 站点数不变、``analysis_quality.end_treatment`` 记录来源口径。

真实数据的强校验（H0A3Z2-DT 的 c1 缓存派生 v1，与独立测出的 v1 缓存
逐站对比 633 站全为 0 差异）不放进单测：那需要 GB 级缓存文件。
"""
import os
import sys
import unittest

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.entity import giant_adapter  # noqa: E402
from core.pp.tube.end_treatment import complete_ends, end_options  # noqa: E402


def _circle(radius, points=48):
    angles = np.linspace(0.0, 2.0 * np.pi, points, endpoint=False)
    return np.c_[radius * np.cos(angles), radius * np.sin(angles)]


def _sections(count=40, step=1.0, start=0.0):
    """合成直管：半径从 10 线性收到 9，壁厚 2.0，站位严格递增。"""
    rows = []
    for i in range(count):
        station = start + i * step
        radius = 10.0 - 1.0 * (i / max(1, count - 1))
        outer = _circle(radius)
        inner = _circle(radius - 2.0)
        rows.append({
            "index": i,
            "cumulative_length": station,
            "position": [0.0, 0.0, station],
            "normal": [0.0, 0.0, 1.0],
            "basis_u": [1.0, 0.0, 0.0],
            "basis_v": [0.0, 1.0, 0.0],
            "outer_2d": outer.tolist(),
            "inner_2d": inner.tolist(),
            "perimeter": float(2.0 * np.pi * radius),
            "wall_thickness": 2.0,
            "measurement_status": "measured",
        })
    return rows


def _analysis(rows, end_treatment="c1"):
    # 端面放在首个/末个站位之外，才能长出「外推端部站位」（真实测量同样如此）
    planes = [
        {"point": [0.0, 0.0, rows[0]["cumulative_length"] - 1.0], "normal": [0.0, 0.0, 1.0]},
        {"point": [0.0, 0.0, rows[-1]["cumulative_length"] + 1.0], "normal": [0.0, 0.0, 1.0]},
    ]
    treated, meta = complete_ends(rows, planes, end_options({"shape_extrapolation": end_treatment}))
    return {
        "sections_full": treated,
        "analysis_quality": {"engine": "synthetic", "end_treatment": meta},
        "center_line_length": treated[-1]["cumulative_length"],
        "algorithm": "A",
        "source_format": "synthetic",
    }


class DeriveAnalysisTest(unittest.TestCase):
    def test_matches_direct_v1_pass(self):
        rows = _sections()
        c1 = _analysis(rows, "c1")
        expected = _analysis(rows, "v1")

        derived = giant_adapter.derive_analysis(c1, "v1")

        self.assertEqual(len(derived["sections_full"]), len(expected["sections_full"]))
        for got, want in zip(derived["sections_full"], expected["sections_full"]):
            self.assertAlmostEqual(got["cumulative_length"], want["cumulative_length"], places=9)
            self.assertAlmostEqual(got["perimeter"], want["perimeter"], places=9)
            self.assertAlmostEqual(got["wall_thickness"], want["wall_thickness"], places=9)
            np.testing.assert_allclose(got["outer_2d"], want["outer_2d"], atol=1e-9)

    def test_keeps_measured_stations_untouched(self):
        rows = _sections()
        c1 = _analysis(rows, "c1")
        derived = giant_adapter.derive_analysis(c1, "v1")

        before = [r for r in c1["sections_full"] if r["measurement_status"] == "measured"]
        after = [r for r in derived["sections_full"] if r["measurement_status"] == "measured"]
        self.assertEqual(len(before), len(after))
        for old, new in zip(before, after):
            self.assertEqual(old["cumulative_length"], new["cumulative_length"])
            self.assertEqual(old["outer_2d"], new["outer_2d"])

    def test_records_source_treatment(self):
        derived = giant_adapter.derive_analysis(_analysis(_sections(), "c1"), "v1")
        meta = derived["analysis_quality"]["end_treatment"]
        self.assertEqual(meta["derived_from"], "c1")
        self.assertEqual(meta["options"]["shape_extrapolation"], "v1")
        self.assertEqual(
            derived["center_line_length"], derived["sections_full"][-1]["cumulative_length"]
        )

    def test_rejects_unknown_treatment_and_short_input(self):
        good = _analysis(_sections(), "c1")
        with self.assertRaises(ValueError):
            giant_adapter.derive_analysis(good, "v9")
        with self.assertRaises(ValueError):
            giant_adapter.derive_analysis({"sections_full": good["sections_full"][:4]}, "v1")


if __name__ == "__main__":
    unittest.main()
