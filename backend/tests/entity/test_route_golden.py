# -*- coding: utf-8 -*-
"""慢速金标准测试：24TK_1324-1 的 ``成型_外径形腔`` 跑通 **c1 与 pipe 两条路**。

流程（真实调用：CLI → orchestrator → routes → OCC/FreeCAD）：

1. 清空 ``.tmp\\s10\\golden``，把 ``entity_out\\_cache`` 里播种好的测量缓存硬链接/复制过去
   （``measure()`` 命中缓存 1–2 s；否则冷跑 5–6 min，见 §11/§12）；
2. ``python backend\\run_entity.py --src gnt管件 --out .tmp\\s10\\golden
   --parts 24TK_1324-1 --models 成型_外径形腔 --routes c1,pipe``；
3. 读 ``_report\\summary.json`` 断言两格几何结果；
4. 用 ``checks.inspect_step`` 把落盘的 STEP 再体检一遍（4 面 / 1 实体 / 0 自由边）。

金标准（§8.1，c1 容差 1 mm³）：体积 1 881 257.533 mm³。
pipe 路是 FreeCADCmd 重蒙皮：本机 2026-11-03 实测 1 881 548.954 mm³（相对 c1 −0.19%），
任务书给的 1 881 548.95 与之吻合，容差放到 50 mm³。
**不断言耗时**：FreeCADCmd 子进程冷启动 + 机器负载会带来几十秒抖动。

跳过方式：``set ENTITY_SKIP_SLOW=1``（或 ``$env:ENTITY_SKIP_SLOW='1'``）。
"""
import json
import os
import shutil
import subprocess
import sys
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
_REPO = os.path.abspath(os.path.join(_BACKEND, os.pardir))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.entity import checks, contracts, orchestrator  # noqa: E402
from unittest import mock  # noqa: E402

PART = "24TK_1324-1"
MODEL = "成型_外径形腔"
OUT_ROOT = os.path.join(_REPO, ".tmp", "s10", "golden")

C1_GOLDEN_VOLUME = 1881257.533  # §8.1，容差 1 mm³
PIPE_EXPECTED_VOLUME = 1881548.95  # 本机实测 1 881 548.954，容差 50 mm³
PIPE_TOLERANCE_MM3 = 50.0
C1_TOLERANCE_MM3 = 1.0

C1_STEP = os.path.join(OUT_ROOT, PART, "10_c1", "%s__c1.step" % MODEL)
PIPE_STEP = os.path.join(OUT_ROOT, PART, "20_pipe", "%s__pipe.step" % MODEL)

# 主流水线（entity_out）的正式交付件：复检类用例优先看它，缺失才回落到测试自己的冒烟输出。
# 注：默认路线下「成型_外径形腔」走 c1，所以 pipe 复检改用默认走 pipe 的「压扁_压扁模型」。
MAIN_OUT_ROOT = os.path.join(_REPO, "entity_out")
PIPE_MODEL = "压扁_压扁模型"
MAIN_PIPE_STEP = os.path.join(MAIN_OUT_ROOT, PART, "20_pipe", "%s__pipe.step" % PIPE_MODEL)


def _delivered_pipe_step():
    for path in (MAIN_PIPE_STEP, PIPE_STEP):
        if os.path.isfile(path):
            return path
    return None

def _skip_slow():
    value = os.environ.get("ENTITY_SKIP_SLOW", "").strip().lower()
    return value not in ("", "0", "false", "no", "off")


def _pipeline_env():
    env = dict(os.environ)
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
        }
    )
    return env


def _seed_measure_cache():
    """把正式目录里播种好的测量缓存给自测目录用（硬链接优先，避免多占 ~68 MB）。"""
    source = os.path.join(_REPO, "entity_out", "_cache", "%s_measured.json" % PART)
    target_dir = os.path.join(OUT_ROOT, "_cache")
    os.makedirs(target_dir, exist_ok=True)
    if not os.path.isfile(source):
        return False
    target = os.path.join(target_dir, os.path.basename(source))
    if os.path.isfile(target):
        return True
    try:
        os.link(source, target)  # 同卷硬链接：不产生新数据
    except OSError:
        shutil.copy2(source, target)
    return True


class GoldenRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if _skip_slow():
            raise unittest.SkipTest("ENTITY_SKIP_SLOW 已设置，跳过真实流水线慢测")
        # 干净目录：避免断点续跑把状态变成 cached，从而掩盖真实几何结果
        shutil.rmtree(OUT_ROOT, ignore_errors=True)
        os.makedirs(OUT_ROOT, exist_ok=True)
        cached = _seed_measure_cache()

        cls.log_path = os.path.join(OUT_ROOT, "run.log")
        command = [
            sys.executable,
            os.path.join(_BACKEND, "run_entity.py"),
            "--src",
            "gnt管件",
            "--out",
            OUT_ROOT,
            "--parts",
            PART,
            "--models",
            MODEL,
            "--routes",
            "c1,pipe",
        ]
        started = time.time()
        with open(cls.log_path, "w", encoding="utf-8") as log_handle:
            log_handle.write("$ %s\n" % " ".join(command))
            log_handle.write("measure_cache_seeded=%s\n\n" % cached)
            log_handle.flush()
            cls.proc = subprocess.run(
                command,
                cwd=_REPO,
                env=_pipeline_env(),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                timeout=2700,  # 冷启动 + 冷 measure 的最坏情况
            )
        cls.elapsed = time.time() - started

        cls.summary_path = os.path.join(OUT_ROOT, "_report", "summary.json")
        if cls.proc.returncode != 0:
            raise AssertionError(
                "流水线退出码 %s（%.1f s），完整日志：%s\n%s"
                % (cls.proc.returncode, cls.elapsed, cls.log_path, cls._log_tail())
            )
        if not os.path.isfile(cls.summary_path):
            raise AssertionError("流水线没有写出汇总：%s" % cls.summary_path)
        with open(cls.summary_path, "r", encoding="utf-8") as handle:
            cls.summary = json.load(handle)
        cls.cells = {
            (cell["part"], cell["key"], cell["route"]): cell
            for cell in cls.summary.get("cells", [])
        }

    @classmethod
    def _log_tail(cls, lines=40):
        try:
            with open(cls.log_path, "r", encoding="utf-8", errors="replace") as handle:
                return "".join(handle.readlines()[-lines:])
        except OSError:
            return ""

    def _cell(self, route):
        cell = self.cells.get((PART, MODEL, route))
        self.assertIsNotNone(cell, "summary 里没有 %s/%s 这一格：%s" % (MODEL, route, sorted(self.cells)))
        return cell

    # -- c1 路 ---------------------------------------------------------------
    def test_c1_cell_matches_golden(self):
        cell = self._cell("c1")
        self.assertEqual(cell["status"], "ok", cell.get("issues"))
        self.assertEqual(cell["faces"], 4)
        # summary.json 的 cells 现在也带 solids（与 report.json 的 RouteResult 对齐），
        # 实体数仍需 STEP 复检用例交叉验证（test_steps_on_disk_reinspect_as_4_faces）。
        self.assertEqual(cell.get("solids"), 1)
        self.assertEqual(cell["free_edges"], 0)
        self.assertEqual(cell["parting_edges"], 2)
        self.assertIs(cell["valid"], True)
        self.assertLessEqual(
            abs(float(cell["volume_mm3"]) - C1_GOLDEN_VOLUME),
            C1_TOLERANCE_MM3,
            "c1 体积 %.3f 偏离金标准 %.3f" % (cell["volume_mm3"], C1_GOLDEN_VOLUME),
        )

    # -- pipe 路 -------------------------------------------------------------
    def test_pipe_cell_matches_expected_volume(self):
        cell = self._cell("pipe")
        self.assertEqual(cell["status"], "ok", cell.get("issues"))
        self.assertEqual(cell["faces"], 4)
        # 同上：cells 现在带 solids，实体数另有 STEP 复检用例。
        self.assertEqual(cell.get("solids"), 1)
        self.assertEqual(cell["free_edges"], 0)
        self.assertIs(cell["valid"], True)
        self.assertLessEqual(
            abs(float(cell["volume_mm3"]) - PIPE_EXPECTED_VOLUME),
            PIPE_TOLERANCE_MM3,
            "pipe 体积 %.3f 偏离期望 %.3f" % (cell["volume_mm3"], PIPE_EXPECTED_VOLUME),
        )

    def test_no_error_cells_in_summary(self):
        counts = self.summary["counts"]
        self.assertEqual(int(counts["error"]), 0, self.summary.get("errors"))
        self.assertEqual(int(counts["total"]), 2)
        self.assertEqual(int(counts["ok"]), 2)

    # -- 落盘 STEP 复检 ------------------------------------------------------
    def test_steps_on_disk_reinspect_as_4_faces(self):
        for route, path in (("c1", C1_STEP), ("pipe", PIPE_STEP)):
            with self.subTest(route=route):
                self.assertTrue(os.path.isfile(path), "缺产物：%s" % path)
                self.assertGreater(os.path.getsize(path), 10_000)
                record = checks.inspect_step(path)
                self.assertEqual(record.faces, 4)
                self.assertEqual(record.solids, 1)
                self.assertEqual(record.free_edges, 0)
                self.assertIs(record.valid, True)
                cell_volume = float(self._cell(route)["volume_mm3"])
                self.assertLessEqual(abs(record.volume_mm3 - cell_volume), 1.0)


class PipeRecheckTest(unittest.TestCase):
    """pipe 路主进程复检回归（不跑几何，直接读主流水线留下的交付 STEP）。

    ``routes/pipe.py`` 末尾会把交付 STEP 丢给 ``checks.safe_inspect_step`` 复核，
    再 ``merge_check`` 覆盖 faces/edges/solids/free_edges/valid/volume/bbox——
    FreeCAD 侧 report 只是被检对象的自述，``checks`` 才是权威口径。
    这里对真实产物断言同一组规范值，确认复检结果确实落到了 ``summary.json`` 的 cell 上。
    """

    UNIFIED_SUMMARY = os.path.join(_REPO, "entity_out", "_report", "summary.json")

    def test_unified_pipe_step_reinspects_as_4_face(self):
        step = _delivered_pipe_step()
        if step is None:
            self.skipTest("主流水线还没跑出 pipe 交付件：%s" % MAIN_PIPE_STEP)
        record = checks.inspect_step(step)
        self.assertEqual(record.faces, 4)
        self.assertEqual(record.solids, 1)
        self.assertEqual(record.free_edges, 0)
        self.assertEqual(record.parting_edges, 2)
        self.assertIs(record.valid, True)
        self.assertGreater(record.volume_mm3 or 0, 0)

    def test_summary_pipe_cell_uses_rechecked_values(self):
        if not os.path.isfile(self.UNIFIED_SUMMARY):
            self.skipTest("主流水线汇总不存在：%s" % self.UNIFIED_SUMMARY)
        with open(self.UNIFIED_SUMMARY, "r", encoding="utf-8") as handle:
            summary = json.load(handle)
        cell = next(
            (
                item
                for item in summary.get("cells", [])
                if item.get("part") == PART
                and item.get("key") == PIPE_MODEL
                and item.get("route") == "pipe"
            ),
            None,
        )
        self.assertIsNotNone(cell, "主流水线 summary 里没有 %s/%s/pipe 这一格" % (PART, PIPE_MODEL))
        # 全量首跑为 ok，之后用 --resume 复跑会把同一格记为 cached；两者都代表产物可用。
        self.assertIn(cell["status"], ("ok", "cached"), cell.get("issues"))
        self.assertEqual(cell["faces"], 4)
        self.assertEqual(cell["free_edges"], 0)
        self.assertEqual(cell["parting_edges"], 2)
        self.assertIs(cell["valid"], True)
        step = _delivered_pipe_step()
        if step is not None:
            record = checks.inspect_step(step)
            self.assertLessEqual(abs(float(cell["volume_mm3"]) - record.volume_mm3), 1.0)


class CachedDowngradeTest(unittest.TestCase):
    """``--resume`` 复用缓存时的复核：致命不合规降级为 error，软问题只记录。

    ``orchestrator._cached_result()`` 会对盘上产物重新 ``_apply_checks``，再拿
    ``checks.hard_issues()`` 判致命违规（面数/实体数/自由边/有效性）——有则把状态从
    ``cached`` 降到 ``error``（error 文本「缓存产物复核不合规：…」）；
    ``checks.soft_issues()``（分型边条数）只进 ``problems``，状态仍是 ``cached``。
    纯逻辑测试：体检函数打桩，不跑几何。
    """

    def setUp(self):
        self.root = os.path.join(_REPO, ".tmp", "s10", "regress", "cache_downgrade")
        shutil.rmtree(self.root, ignore_errors=True)
        self.part = "S10_FAKE_PART"
        self.spec = contracts.ModelSpec(
            part=self.part,
            key=MODEL,
            step=os.path.join(self.root, self.part, "00_models", "%s.stp" % MODEL),
            end_treatment="c1",
            out_dir=os.path.join(self.root, self.part),
        )
        os.makedirs(os.path.join(self.root, self.part, "10_c1"), exist_ok=True)
        # 缓存复用只要求产物文件存在；内容无关紧要（体检被 mock）
        with open(self.spec.out_path("c1"), "wb") as handle:
            handle.write(b"ISO-10303-21;\nEND-ISO-10303-21;\n")
        self.ctx = contracts.PipelineContext(
            src_dir=os.path.join(_REPO, "gnt管件"),
            out_root=self.root,
            parts=[self.part],
            log=lambda *_args, **_kwargs: None,
        )

    def _cached(self):
        return orchestrator._cached_result(self.spec, "c1", self.ctx, lambda *_args, **_kwargs: None)

    def test_hard_issue_cached_artifact_becomes_error(self):
        with mock.patch.object(checks, "hard_issues", return_value=["面数 6 ≠ 4"]), \
                mock.patch.object(checks, "soft_issues", return_value=[]):
            result = self._cached()
        self.assertEqual(result.status, "error")
        self.assertIn("缓存产物复核不合规", result.error or "")
        self.assertIn("面数 6 ≠ 4", result.error or "")

    def test_soft_issue_cached_artifact_stays_cached(self):
        with mock.patch.object(checks, "hard_issues", return_value=[]), \
                mock.patch.object(checks, "soft_issues", return_value=["共享分型边 4 ≠ 2"]):
            result = self._cached()
        self.assertEqual(result.status, "cached")
        self.assertIsNone(result.error)
        self.assertIn("共享分型边 4 ≠ 2", "；".join(result.problems or []))

    def test_conforming_cached_artifact_stays_cached(self):
        with mock.patch.object(checks, "hard_issues", return_value=[]), \
                mock.patch.object(checks, "soft_issues", return_value=[]):
            result = self._cached()
        self.assertEqual(result.status, "cached")
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
