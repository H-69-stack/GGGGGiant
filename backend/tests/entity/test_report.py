# -*- coding: utf-8 -*-
"""报告落盘契约测试：``report.write_part_report`` / ``report.write_summary``。

用合成 ``PartReport``（不跑几何）验证：

* ``report.json`` / ``summary.json`` 可被 ``json.loads``；
* ``report.md`` 含件号与模型名，``summary.md`` 含件号与模型名；
* 两个文件都是 UTF-8 **无 BOM**（``utf-8-sig`` 不成立、``\ufeff`` 不出现）；
* ``counts`` 的 ok / error / skipped / cached / stopped 计数与 ``matrix`` / ``cells`` / ``errors`` 一致。
* ``stopped`` 只记自己那一格，**不算 error**（协作停止 ≠ 流水线报错）。

落盘目录统一走 ``.tmp\\s10\\report``，不碰 ``entity_out`` 正式产物。
"""
import json
import os
import sys
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
_REPO = os.path.abspath(os.path.join(_BACKEND, os.pardir))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.entity import contracts, report as report_module  # noqa: E402

PART = "24TK_1324-1"
SRC_STEP = os.path.join(_REPO, "gnt管件", f"{PART}.stp")


def _result(key, route, status, **kwargs):
    payload = dict(part=PART, key=key, route=route, status=status)
    payload.update(kwargs)
    return contracts.RouteResult(**payload)


def _build_report():
    """1 ok + 1 error + 1 skipped + 1 cached + 1 stopped，覆盖计数与 5 个状态。"""
    part_report = contracts.PartReport(
        part=PART,
        source_step=SRC_STEP,
        end_treatment="c1",
        measured_path=os.path.join(_REPO, "entity_out", "_cache", f"{PART}_measured.json"),
        finished=contracts.now_iso(),
        seconds=15.1,
        logs=["[24TK_1324-1] 复用测量缓存（761 个站位）"],
        models={"成型_外径形腔": os.path.join("entity_out", PART, "00_models", "shaping_cavity.stp")},
    )
    part_report.add(
        _result(
            "成型_外径形腔",
            "c1",
            "ok",
            step=os.path.join("entity_out", PART, "10_c1", "成型_外径形腔__c1.step"),
            volume_mm3=1881257.533,
            ref_volume_mm3=1880895.671,
            volume_error_pct=0.0192,
            faces=4,
            edges=12,
            solids=1,
            free_edges=0,
            parting_edges=2,
            valid=True,
            direction="Z",
            direction_label="Z",
            rule="symmetry",
            sections_used=376,
            sections_total=376,
            seconds=15.051,
        )
    )
    part_report.add(
        _result(
            "扩口_扩口腔体",
            "c1",
            "error",
            error="RuntimeError: 输入 STEP 不存在：D:/no/such/belling_cavity.stp",
            seconds=0.004,
        )
    )
    part_report.add(
        _result(
            "压扁_压扁模型",
            "pipe",
            "skipped",
            logs=["[24TK_1324-1] pipe 路不可用：freecadcmd 缺失"],
        )
    )
    part_report.add(
        _result(
            "压弯_压弯模型",
            "pipe",
            "cached",
            step=os.path.join("entity_out", PART, "20_pipe", "压弯_压弯模型__pipe.step"),
            volume_mm3=1933013.175,
            faces=4,
            edges=12,
            solids=1,
            free_edges=0,
            parting_edges=2,
            valid=True,
            seconds=0.0,
        )
    )
    part_report.add(
        _result(
            "扩口_扩口芯轴",
            "pipe",
            "stopped",
            error="KeyboardInterrupt：收到协作停止请求，未开始这一格",
            seconds=0.0,
        )
    )
    return part_report


def _read_text(path):
    """严格按 UTF-8 解码（顺带证明没有混杂其它编码）。"""
    data = Path(path).read_bytes()
    return data, data.decode("utf-8")


class PartReportWriteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = _build_report()
        cls.out_dir = os.path.join(_REPO, ".tmp", "s10", "report", "part")
        cls.written = report_module.write_part_report(cls.report, cls.out_dir)
        cls.json_path = cls.written["json"]
        cls.md_path = cls.written["md"]

    def test_files_exist_and_returned(self):
        self.assertEqual(self.written["schema"], "entity-part-report/1")
        self.assertTrue(self.written["written"])
        self.assertTrue(os.path.isfile(self.json_path), self.json_path)
        self.assertTrue(os.path.isfile(self.md_path), self.md_path)
        self.assertEqual(
            sorted(self.written["files"]),
            sorted([self.json_path, self.md_path]),
        )
        self.assertGreater(self.written["json_bytes"], 0)
        self.assertGreater(self.written["md_bytes"], 0)

    def test_json_parses_and_carries_counts(self):
        payload = json.loads(Path(self.json_path).read_text(encoding="utf-8"))
        self.assertEqual(payload["part"], PART)
        self.assertEqual(payload["schema"], "entity-part-report/1")
        counts = payload["counts"]
        self.assertEqual(counts["total"], 5)
        self.assertEqual(counts["ok"], 1)
        self.assertEqual(counts["error"], 1)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["cached"], 1)
        self.assertEqual(counts["stopped"], 1)
        self.assertEqual(counts["other"], 0)
        self.assertAlmostEqual(counts["seconds"], 15.055, places=3)
        self.assertEqual(len(payload["results"]), 5)
        self.assertEqual(payload["out_dir"], os.path.abspath(self.out_dir))
        self.assertIn("problems", payload)
        self.assertEqual(payload["environment"]["python"], "%d.%d.%d" % sys.version_info[:3])

    def test_counts_returned_match_json(self):
        payload = json.loads(Path(self.json_path).read_text(encoding="utf-8"))
        self.assertEqual(self.written["counts"], payload["counts"])
        self.assertEqual(self.written["results"], 5)

    def test_conformance_totals(self):
        payload = json.loads(Path(self.json_path).read_text(encoding="utf-8"))
        conformance = payload["conformance"]
        self.assertEqual(conformance["faces_ne_4"], 0)
        self.assertEqual(conformance["solids_ne_1"], 0)
        self.assertEqual(conformance["free_edges_ne_0"], 0)
        self.assertEqual(conformance["parting_ne_2"], 0)
        self.assertEqual(conformance["invalid"], 0)
        # error 行的原因必须进 problems（前缀带 key/route）
        problems = "\n".join(payload["problems"])
        self.assertIn("扩口_扩口腔体/c1", problems)
        self.assertIn("输入 STEP 不存在", problems)

    def test_markdown_contains_part_and_model_names(self):
        _data, text = _read_text(self.md_path)
        self.assertIn(PART, text)
        self.assertTrue(text.startswith("# Giant-Unified 实体化流水线报告"))
        for needle in ("成型_外径形腔", "扩口腔体", "压扁模型", "压弯模型", "c1", "pipe"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertIn("合规与问题", text)

    def test_files_are_utf8_without_bom(self):
        for path in (self.json_path, self.md_path):
            with self.subTest(path=os.path.basename(path)):
                data, _text = _read_text(path)
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "带 BOM：%s" % path)
                self.assertNotIn("\ufeff", _text)
        md_data, _ = _read_text(self.md_path)
        self.assertNotIn(b"\r\n", md_data)  # 统一 \n 行尾

    def test_reversed_positional_order_still_writes(self):
        # 早期 orchestrator 写成 write_part_report(out_dir, report)，契约要求兼容
        other_dir = os.path.join(_REPO, ".tmp", "s10", "report", "part_reversed")
        written = report_module.write_part_report(other_dir, self.report)
        self.assertTrue(os.path.isfile(written["json"]))
        self.assertEqual(written["part"], PART)


class SummaryWriteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = _build_report()
        cls.out_root = os.path.join(_REPO, ".tmp", "s10", "report", "root")
        cls.written = report_module.write_summary([cls.report], cls.out_root)
        cls.json_path = cls.written["json"]
        cls.md_path = cls.written["md"]

    def test_summary_lands_in_report_dir(self):
        self.assertEqual(self.written["schema"], "entity-summary/1")
        self.assertEqual(
            os.path.normcase(os.path.abspath(self.json_path)),
            os.path.normcase(os.path.abspath(os.path.join(self.out_root, "_report", "summary.json"))),
        )
        self.assertTrue(os.path.isfile(self.md_path))

    def test_summary_json_content(self):
        payload = json.loads(Path(self.json_path).read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "entity-summary/1")
        self.assertEqual(payload["parts"], [PART])
        self.assertEqual(tuple(payload["routes"]), ("c1", "pipe"))
        self.assertEqual(len(payload["models"]), 5)
        counts = payload["counts"]
        self.assertEqual((counts["total"], counts["ok"], counts["error"], counts["skipped"], counts["cached"]),
                         (5, 1, 1, 1, 1))
        # stopped 是独立状态：不算 error，也不能进 other
        self.assertEqual(counts["stopped"], 1)
        self.assertEqual(self.written["counts"], counts)
        self.assertEqual(self.written["parts"], 1)
        self.assertEqual(self.written["results"], 5)
        self.assertEqual(self.written["errors"], 1)
        self.assertEqual(len(payload["cells"]), 5)
        statuses = sorted((cell["route"], cell["status"]) for cell in payload["cells"])
        self.assertEqual(
            statuses,
            sorted([("c1", "ok"), ("c1", "error"), ("pipe", "skipped"), ("pipe", "cached"), ("pipe", "stopped")]),
        )
        self.assertEqual(payload["matrix"]["c1"][PART]["成型_外径形腔"], "ok")
        self.assertEqual(payload["matrix"]["pipe"][PART]["压弯_压弯模型"], "cached")
        errors = payload["errors"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["key"], "扩口_扩口腔体")
        self.assertEqual(errors[0]["route"], "c1")
        self.assertIn("输入 STEP 不存在", errors[0]["error"])
        ok_cell = next(cell for cell in payload["cells"] if cell["status"] == "ok")
        self.assertAlmostEqual(ok_cell["volume_mm3"], 1881257.533, places=3)
        self.assertEqual(ok_cell["faces"], 4)
        self.assertTrue(ok_cell["valid"])
        self.assertEqual(payload["expectations"]["faces"], 4)
        self.assertEqual(payload["expectations"]["parting_edges"], 2)

    def test_summary_markdown(self):
        _data, text = _read_text(self.md_path)
        self.assertTrue(text.startswith("# Giant-Unified 实体化流水线汇总报告"))
        self.assertIn(PART, text)
        for needle in ("成型_外径形腔", "扩口_扩口腔体", "压扁_压扁模型", "压弯_压弯模型"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)

    def test_summary_utf8_without_bom(self):
        for path in (self.json_path, self.md_path):
            with self.subTest(path=os.path.basename(path)):
                data, _text = _read_text(path)
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "带 BOM：%s" % path)

    def test_reversed_positional_order_still_writes(self):
        other_root = os.path.join(_REPO, ".tmp", "s10", "report", "root_reversed")
        written = report_module.write_summary(other_root, [self.report])
        self.assertTrue(os.path.isfile(written["json"]))
        self.assertEqual(written["parts"], 1)


class ReportErrorTest(unittest.TestCase):
    def test_part_report_requires_report_and_dir(self):
        with self.assertRaises(TypeError):
            report_module.write_part_report()
        with self.assertRaises(TypeError):
            report_module.write_part_report(_build_report())

    def test_summary_requires_reports(self):
        with self.assertRaises(TypeError):
            report_module.write_summary()
        with self.assertRaises(TypeError):
            report_module.write_summary(os.path.join(_REPO, ".tmp", "s10", "report", "x"))


class StoppedStatusTest(unittest.TestCase):
    """``stopped``（协作停止）计数口径：单列一项，不算 error，也不进 other。"""

    @classmethod
    def setUpClass(cls):
        cls.out_root = os.path.join(_REPO, ".tmp", "s10", "report", "root_stopped")
        cls.written = report_module.write_summary([_build_report()], cls.out_root)

    def test_status_counts_has_stopped_slot(self):
        counts = report_module._status_counts(
            [{"status": "stopped"}, {"status": "stopped"}, {"status": "ok"}]
        )
        self.assertEqual(counts["stopped"], 2)
        self.assertEqual(counts["error"], 0)
        self.assertEqual(counts["other"], 0)
        self.assertEqual(counts["total"], 3)

    def test_summary_keeps_stopped_out_of_errors(self):
        payload = json.loads(Path(self.written["json"]).read_text(encoding="utf-8"))
        counts = payload["counts"]
        self.assertEqual(counts["stopped"], 1)
        self.assertEqual(counts["error"], 1)
        stopped_cells = [cell for cell in payload["cells"] if cell["status"] == "stopped"]
        self.assertEqual(len(stopped_cells), 1)
        self.assertEqual(stopped_cells[0]["key"], "扩口_扩口芯轴")
        # errors[] 只收 status == "error" 的格，stopped 不许混进去
        self.assertEqual(
            [(item["key"], item["route"]) for item in payload["errors"]],
            [("扩口_扩口腔体", "c1")],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
