# -*- coding: utf-8 -*-
"""契约测试：``core.entity.contracts`` 的模型注册表、端部口径、参考路与产物路径。

对应 ``ENTITY_PIPELINE.md`` §5（5 类模型）、§6（两条路）、§8.1（金标准口径）、§9（签名）。

跑法（仓库根目录）::

    D:\\python\\envs\\occ\\Scripts\\python.exe -m unittest discover -s backend\\tests\\entity -t backend -v
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
if _BACKEND not in sys.path:  # 允许从仓库根或任意 cwd 跑
    sys.path.insert(0, _BACKEND)

from core.entity import contracts  # noqa: E402

#: (key, op, op_label, name, contour, slug, c1_kind) —— 顺序即注册表顺序（§5）。
EXPECTED_MODELS = (
    ("成型_外径形腔", "shaping", "成型", "外径形腔", "outer", "shaping_cavity", "measured_outer"),
    ("扩口_扩口腔体", "belling", "扩口", "扩口腔体", "solid", "belling_cavity", "selfref"),
    ("扩口_扩口芯轴", "belling", "扩口", "扩口芯轴", "solid", "belling_mandrel", "selfref"),
    ("压扁_压扁模型", "flattening", "压扁", "压扁模型", "solid", "flattening_model", "selfref"),
    ("压弯_压弯模型", "bending", "压弯", "压弯模型", "solid", "bending_model", "selfref"),
)

#: 本流水线的 5 个普通件（端部口径 c1）与 1 个参考件（v1）。
PLAIN_PARTS = ("24TK_1324-1", "24TK_1324-2", "24TK_1324-31", "24TK_1442-1", "H0A2Z3-DT")
REFERENCE_PART = "24TK_2052-2"


class ModelRegistryTest(unittest.TestCase):
    def test_routes_and_end_treatments(self):
        self.assertEqual(tuple(contracts.ROUTES), ("c1", "pipe"))
        self.assertEqual(tuple(contracts.END_TREATMENTS), ("c1", "v1"))
        self.assertEqual(contracts.DEFAULT_END_TREATMENT, "c1")

    def test_five_models_in_registry_order(self):
        self.assertEqual(len(contracts.MODELS), 5)
        self.assertEqual(tuple(m.key for m in contracts.MODELS), tuple(row[0] for row in EXPECTED_MODELS))
        self.assertEqual(tuple(contracts.MODEL_KEYS), tuple(row[0] for row in EXPECTED_MODELS))

    def test_model_def_fields(self):
        for row in EXPECTED_MODELS:
            key, op, op_label, name, contour, slug, c1_kind = row
            with self.subTest(key=key):
                defn = contracts.model_def(key)
                self.assertIsInstance(defn, contracts.ModelDef)
                self.assertEqual(defn.key, key)
                self.assertEqual(defn.op, op)
                self.assertEqual(defn.op_label, op_label)
                self.assertEqual(defn.name, name)
                self.assertEqual(defn.contour, contour)
                self.assertEqual(defn.slug, slug)
                self.assertEqual(defn.c1_kind, c1_kind)

    def test_model_by_key_points_at_the_same_objects(self):
        for row in EXPECTED_MODELS:
            with self.subTest(key=row[0]):
                self.assertIs(contracts.MODEL_BY_KEY[row[0]], contracts.model_def(row[0]))

    def test_unknown_model_key_raises_keyerror_with_chinese_hint(self):
        with self.assertRaises(KeyError) as caught:
            contracts.model_def("成型_不存在的腔")
        message = str(caught.exception)
        self.assertIn("未知模型", message)
        # 中文提示里应带上合法键之一，便于排错
        self.assertIn("成型_外径形腔", message)

    def test_slugs_unique_ascii_and_no_mold_marker(self):
        slugs = [row[5] for row in EXPECTED_MODELS]
        self.assertEqual(len(set(slugs)), len(slugs))
        for slug in slugs:
            with self.subTest(slug=slug):
                self.assertTrue(slug.isascii())
                self.assertRegex(slug, r"^[a-z][a-z0-9_]+$")
        # 反切模具已下线（§5 / §12）：注册表里不得出现该标记
        for defn in contracts.MODELS:
            with self.subTest(key=defn.key):
                self.assertNotIn(contracts.MOLD_MARKER, defn.key + defn.name + defn.slug)

    def test_model_spec_defn_property(self):
        spec = contracts.ModelSpec(
            part="24TK_1324-1",
            key="扩口_扩口腔体",
            step=os.path.join("x", "00_models", "扩口_扩口腔体.stp"),
            end_treatment="c1",
            out_dir=os.path.join("x", "24TK_1324-1"),
        )
        self.assertEqual(spec.defn.slug, "belling_cavity")
        self.assertEqual(spec.defn.op, "belling")


class EndTreatmentTest(unittest.TestCase):
    def test_reference_part_uses_v1(self):
        self.assertEqual(contracts.end_treatment_for(REFERENCE_PART), "v1")

    def test_plain_parts_use_c1(self):
        for part in PLAIN_PARTS:
            with self.subTest(part=part):
                self.assertEqual(contracts.end_treatment_for(part), "c1")

    def test_accepts_key_keyword_argument(self):
        # 第二参 key= 是契约的一部分：目前口径只看件号，传任意模型键都得同一答案
        self.assertEqual(contracts.end_treatment_for(REFERENCE_PART, key="成型_外径形腔"), "v1")
        self.assertEqual(contracts.end_treatment_for(REFERENCE_PART, key="压弯_压弯模型"), "v1")
        self.assertEqual(contracts.end_treatment_for("24TK_1324-1", key="成型_外径形腔"), "c1")
        self.assertEqual(
            contracts.end_treatment_for(part="H0A2Z3-DT", key="扩口_扩口腔体"), "c1"
        )

    def test_reference_route_only_for_2052_2_outer_cavity(self):
        self.assertTrue(contracts.uses_reference_route(REFERENCE_PART, "成型_外径形腔"))
        # 同件的其它模型仍走普通 c1（自参考 / 成型口径）
        for key in contracts.MODEL_KEYS:
            with self.subTest(part=REFERENCE_PART, key=key):
                self.assertEqual(
                    contracts.uses_reference_route(REFERENCE_PART, key),
                    key == "成型_外径形腔",
                )
        # 其它件即使是同一个模型也不算参考路
        for part in PLAIN_PARTS:
            with self.subTest(part=part):
                self.assertFalse(contracts.uses_reference_route(part, "成型_外径形腔"))

    def test_reference_parts_and_models_constants(self):
        self.assertEqual(tuple(contracts.REFERENCE_PARTS), (REFERENCE_PART,))
        self.assertEqual(tuple(contracts.REFERENCE_MODELS), ("成型_外径形腔",))


class ModelSpecOutPathTest(unittest.TestCase):
    def _spec(self, key="成型_外径形腔"):
        part_dir = os.path.join(os.path.abspath(os.path.join(".tmp", "s10")), "24TK_1324-1")
        return contracts.ModelSpec(
            part="24TK_1324-1",
            key=key,
            step=os.path.join(part_dir, "00_models", f"{key}.stp"),
            end_treatment="c1",
            out_dir=part_dir,
        )

    def test_c1_lands_in_10_c1(self):
        spec = self._spec()
        path = spec.out_path("c1")
        self.assertEqual(
            path,
            os.path.join(spec.out_dir, "10_c1", "成型_外径形腔__c1.step"),
        )

    def test_pipe_lands_in_20_pipe(self):
        spec = self._spec()
        path = spec.out_path("pipe")
        self.assertEqual(
            path,
            os.path.join(spec.out_dir, "20_pipe", "成型_外径形腔__pipe.step"),
        )

    def test_unknown_route_raises_keyerror(self):
        spec = self._spec()
        for route in ("v1", "", "C1"):
            with self.subTest(route=route):
                with self.assertRaises(KeyError):
                    spec.out_path(route)

    def test_route_out_dirs_are_distinct_and_end_with_step(self):
        spec = self._spec("压弯_压弯模型")
        c1, pipe = spec.out_path("c1"), spec.out_path("pipe")
        self.assertNotEqual(c1, pipe)
        self.assertTrue(c1.endswith(".step"))
        self.assertTrue(pipe.endswith(".step"))


class HelperContractTest(unittest.TestCase):
    """§9 里被 orchestrator / report 依赖的薄契约。"""

    def test_route_result_ok_property(self):
        result = contracts.RouteResult(part="p", key="k", route="c1", status="ok")
        self.assertTrue(result.ok)
        result.status = "error"
        self.assertFalse(result.ok)

    def test_result_from_dict_ignores_unknown_fields(self):
        restored = contracts.result_from_dict(
            {
                "part": "24TK_1324-1",
                "key": "成型_外径形腔",
                "route": "pipe",
                "status": "ok",
                "volume_mm3": 1881548.954,
                "not_a_field": {"x": 1},
            }
        )
        self.assertIsInstance(restored, contracts.RouteResult)
        self.assertEqual(restored.status, "ok")
        self.assertAlmostEqual(restored.volume_mm3, 1881548.954, places=3)
        self.assertFalse(hasattr(restored, "not_a_field"))

    def test_json_safe_numpy_scalars(self):
        try:
            import numpy as np
        except Exception:  # pragma: no cover - numpy 缺失时跳过
            self.skipTest("numpy 不可用")
        payload = {"volume": np.float64(1.5), "faces": np.int64(4), "arr": np.arange(3.0)}
        safe = contracts.json_safe(payload)
        self.assertEqual(safe["volume"], 1.5)
        self.assertEqual(safe["faces"], 4)
        self.assertEqual(list(safe["arr"]), [0.0, 1.0, 2.0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
