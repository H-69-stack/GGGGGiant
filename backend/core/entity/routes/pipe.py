# -*- coding: utf-8 -*-
"""pipe 路：FreeCAD 重蒙皮（``solidify.process_file``）。

调用链（``ENTITY_PIPELINE.md`` §6.2）::

    主进程：分型方向（directions.resolve_for_category + selfref.sections_from_step）
        ↓ options['direction'] = [x,y,z]（算不出来就 "auto"，交 FreeCAD 自算）
    FreeCADCmd 无头（默认）或 GUI 桥 18766
        ↓ PipeSolidBridge.solidify.process_file(input, output_dir, options)
    <out>/<part>/20_pipe/<模型键>__pipe.step

本文件只做「编排 + 归一化」，几何算法一律在 FreeCAD 端（不改 solidify.py）：
* 找不到 freecadcmd / 超时 / status=error → 返回 status="error" 的 RouteResult，**不抛**；
* 18766 被占但不是 FreeCAD 桥 → 自动降级 headless；
* 分型方向的依赖模块（S3 ``selfref`` / S5 ``directions``）还没就绪时同样降级为
  ``direction="auto"``，把这行原因写进 logs，绝不让 pipe 路整体失败。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from .. import checks, settings
from ..contracts import RouteResult

#: solidify.process_file 认识（且会写进 report['options']）的键
_SOLIDIFY_KEYS = ("direction", "half_points", "max_sections", "export_stl",
                  "open_in_freecad")

#: 规范值（§8 只记录不判失败）
_EXPECT_FACES = 4
_EXPECT_PARTING_EDGES = 2


# --------------------------------------------------------------------------- 可用性
def available() -> tuple[bool, str]:
    """(pipe 路是否可用, freecadcmd 路径/原因)。只查文件，不启动进程。"""
    try:
        from ..vendor.pss import headless
    except Exception as exc:  # noqa: BLE001
        return False, "vendor/pss 导入失败：%s" % exc
    ok, why = headless.available()
    if not ok:
        return False, why
    return True, why


# --------------------------------------------------------------------------- 方向
def _norm_vector(vec):
    """把 [x,y,z] / (x,y,z) 规整成单位向量；不合法返回 None。"""
    try:
        if not isinstance(vec, (list, tuple)) or len(vec) != 3:
            return None
        out = [float(vec[0]), float(vec[1]), float(vec[2])]
    except (TypeError, ValueError):
        return None
    norm = sum(v * v for v in out) ** 0.5
    if norm <= 1e-12:
        return None
    return [round(v / norm, 9) for v in out]


def _requested_direction(ctx) -> str:
    opts = getattr(ctx, "options", None) or {}
    req = opts.get("direction") or settings.PIPE_OPTIONS.get("direction") or "auto"
    return str(req)


def _direction_from_spec(spec, requested="auto", log=None):
    """主进程算分型方向。

    :returns: ``(direction_option, label, rule, note)``
        * 成功：``([x,y,z], 'Z±', 'symmetry', None)``
        * 降级：``("auto", None, None, "原因")`` —— 交 FreeCAD 自算
    """
    req = str(requested or "auto").strip()
    if req.upper() in ("X", "Y", "Z"):
        axis = req.upper()
        vec = [0.0, 0.0, 0.0]
        vec["XYZ".index(axis)] = 1.0
        return vec, "%s（手动）" % axis, "fixed", None

    # 先查便宜的：directions 没就绪就别去读 28MB STEP 算截面（那一步在 OCC 里很贵）
    try:
        from ..directions import resolve_for_category    # S5 并行开发
    except Exception as exc:  # noqa: BLE001
        return "auto", None, None, "directions 不可用（S5 未就绪），方向交 FreeCAD 自算：%s" % exc

    try:
        from .selfref import sections_from_step          # S3 并行开发
    except Exception as exc:  # noqa: BLE001
        return "auto", None, None, "selfref 不可用，方向交 FreeCAD 自算：%s" % exc
    t_rings = time.time()
    try:
        rings = sections_from_step(spec.step)
    except Exception as exc:  # noqa: BLE001
        return "auto", None, None, "selfref.sections_from_step 失败：%s" % exc
    if not rings:
        return "auto", None, None, "selfref.sections_from_step 返回空截面"
    if callable(log):
        log("selfref 截面：%d 个环，耗时 %.2fs" % (len(rings), time.time() - t_rings))

    categories = [getattr(spec, "key", None)]
    try:
        categories.append(spec.defn.op)                  # 名称兜底：shaping/belling/...
    except Exception:  # noqa: BLE001
        pass
    categories = [c for c in categories if c]

    last = None
    for category in categories:
        try:
            info = resolve_for_category(category, rings, requested=req)
        except Exception as exc:  # noqa: BLE001
            last = "resolve_for_category(%r)：%s" % (category, exc)
            continue
        vec = _norm_vector((info or {}).get("vector"))
        if vec is None:
            last = "resolve_for_category(%r) 未给出向量：%r" % (category, info)
            continue
        return vec, (info or {}).get("label"), (info or {}).get("rule"), None
    return "auto", None, None, "directions 无结果（%s）" % (last or "未知")


# --------------------------------------------------------------------------- 选项
def _pipe_options(ctx, direction) -> dict:
    opts = dict(settings.PIPE_OPTIONS)                   # 默认值（settings 单一来源）
    for key, value in (getattr(ctx, "options", None) or {}).items():
        if key in _SOLIDIFY_KEYS and key != "direction":
            opts[key] = value
    opts = {k: opts.get(k) for k in _SOLIDIFY_KEYS if k in opts}
    opts["direction"] = direction
    if opts.get("open_in_freecad") is None:
        opts["open_in_freecad"] = False
    return opts


def _pipe_mode(ctx) -> str:
    mode = ((getattr(ctx, "options", None) or {}).get("pipe_mode")
            or os.environ.get("PIPESOLID_MODE") or "headless")
    mode = str(mode).strip().lower()
    return mode if mode in ("headless", "bridge", "auto") else "headless"


# --------------------------------------------------------------------------- 执行
def _run_bridge(spec, out_dir, opts, logs):
    """走 GUI 桥 18766；失败抛异常由调用方降级。"""
    from ..vendor.pss import bridge_client
    ok, info = bridge_client.probe()
    if not ok:
        raise bridge_client.BridgeUnavailable(
            "18766 不可用（listening=%s）%s" % (info.get("listening"), info.get("detail")))
    logs.append("pipe：走 GUI 桥 %s" % info.get("url"))
    report = bridge_client.Bridge().process(spec.step, opts, out_dir,
                                            open_doc=bool(opts.get("open_in_freecad")))
    if not isinstance(report, dict) or report.get("status") != "ok":
        raise RuntimeError("GUI 桥返回失败：%s"
                           % ((report or {}).get("message") or report))
    report.setdefault("mode", "bridge")
    report.setdefault("seconds_process", None)
    return report


def _freecad_timeout() -> float:
    """FreeCADCmd 超时（默认 1200s，可用 PIPESOLID_TIMEOUT 覆盖）。"""
    try:
        return float(os.environ.get("PIPESOLID_TIMEOUT", "1200"))
    except ValueError:
        return 1200.0


def _run_headless(spec, out_dir, opts, ctx, logs):
    from ..vendor.pss import config as pss_config
    from ..vendor.pss import headless
    try:
        jobs_dir = pss_config.jobs_dir(getattr(ctx, "out_root", None))
    except Exception:  # noqa: BLE001
        jobs_dir = None
    return headless.run(spec.step, out_dir, opts, log=logs.append, jobs_dir=jobs_dir,
                        timeout=_freecad_timeout(), label="freecad:%s" % getattr(spec, "part", ""))


# --------------------------------------------------------------------------- 归一化
def _problems(result: RouteResult) -> list:
    bad = []
    if result.faces != _EXPECT_FACES:
        bad.append("faces=%s（应 4）" % result.faces)
    if result.solids != 1:
        bad.append("solids=%s（应 1）" % result.solids)
    if result.free_edges not in (0, None):
        bad.append("free_edges=%s（应 0）" % result.free_edges)
    if result.parting_edges not in (_EXPECT_PARTING_EDGES, None):
        bad.append("分型边=%s（应 2）" % result.parting_edges)
    if result.valid is False:
        bad.append("实体无效（isValid=False）")
    if result.volume_error_pct is not None and abs(result.volume_error_pct) > 1.0:
        bad.append("体积偏差 %+.2f%%（>1%%）" % result.volume_error_pct)
    return bad


def _normalize(spec, report, *, dst_step, dst_stl, direction_option, label, rule,
               logs, seconds, extra_artifacts=None) -> RouteResult:
    res = report.get("result") or {}
    src = report.get("source") or {}
    stats = report.get("shape_stats") or {}
    opts = report.get("options") or {}

    # 方向：优先用 FreeCAD 实际使用的向量（parting_dir），标签优先用主进程算的
    used_dir = _norm_vector(res.get("parting_dir")) or (
        direction_option if isinstance(direction_option, list) else None)
    # 包围盒：优先用 OCC 精确盒（optimalBoundingBox）。BoundBox 对 BSpline 面是保守值，
    # 会虚报毫米级「过冲」（实测该件 3.27 mm vs 精确值 0.003–0.026 mm），两份都留档。
    bbox_used = stats.get("bbox_optimal") or res.get("bbox") or stats.get("bbox")
    dev_used = stats.get("bbox_dev_optimal")
    if dev_used is None:
        dev_used = res.get("bbox_dev")
    volume = res.get("volume")
    result = RouteResult(
        part=spec.part,
        key=spec.key,
        route="pipe",
        status="ok",
        step=str(dst_step) if dst_step else None,
        stl=str(dst_stl) if dst_stl else None,
        volume_mm3=volume,
        ref_volume_mm3=src.get("volume_est"),
        volume_error_pct=res.get("volume_error_pct"),
        faces=res.get("faces"),
        edges=res.get("edges"),
        solids=stats.get("solids", 1),
        free_edges=stats.get("free_edges"),
        parting_edges=res.get("parting_edges"),
        valid=res.get("valid"),
        bbox=bbox_used,
        ref_bbox=src.get("bbox"),
        bbox_dev_mm=dev_used,
        direction=used_dir,
        direction_label=label or res.get("direction_label"),
        rule=rule,
        sections_used=res.get("sections"),
        sections_total=res.get("sections_total"),
        attempts=res.get("attempts"),
        seconds=round(seconds, 3),
        logs=list(logs) + list(report.get("logs") or []),
    )
    artifacts = {
        "mode": report.get("mode", "headless"),
        "input": report.get("input") or str(spec.step),
        "freecad_step": (report.get("outputs") or {}).get("step"),
        "freecad_stl": (report.get("outputs") or {}).get("stl"),
        "freecad_report_json": report.get("_report_json"),
        "job_json": report.get("_job_path"),
        "result_json": report.get("_result_path"),
        "seconds_process": report.get("seconds_process"),
        "seconds_in_freecad": report.get("seconds"),
        "seconds_cold_start": _cold_start(report),
        "timings": res.get("timings"),
        "closed": stats.get("closed"),
        "bbox_boundbox": stats.get("bbox") or res.get("bbox"),
        "bbox_dev_boundbox": stats.get("bbox_dev_boundbox", res.get("bbox_dev")),
        "bbox_optimal": stats.get("bbox_optimal"),
        "bbox_dev_optimal": stats.get("bbox_dev_optimal"),
        "bbox_note": "bbox_dev 取 optimalBoundingBox（精确）；boundbox 为 OCC 保守值，含 BSpline 控制点过冲",
        "direction_option": direction_option if isinstance(direction_option, str)
        else list(direction_option or []),
        "solidify_options": opts,
        "source_faces": src.get("faces"),
        "source_edges": src.get("edges"),
        "source_solids": src.get("solids"),
        "source_volume": src.get("volume"),
        "source_volume_est": src.get("volume_est"),
        "source_sections": src.get("sections"),
    }
    if extra_artifacts:
        artifacts.update(extra_artifacts)
    result.artifacts = {k: v for k, v in artifacts.items() if v is not None}
    result.problems = _problems(result)
    return result


def _cold_start(report) -> float | None:
    """子进程墙钟 − FreeCAD 内几何耗时 ≈ 进程冷启动 + import 开销。"""
    wall = report.get("seconds_process")
    inner = report.get("seconds")
    try:
        if wall is None or inner is None:
            return None
        return round(float(wall) - float(inner), 3)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- 入口
def run(spec, ctx) -> RouteResult:
    """pipe 路：FreeCAD 重蒙皮一个模型。永远返回 RouteResult（失败 status="error"）。"""
    t0 = time.time()
    logs = []
    part = getattr(spec, "part", "?")
    key = getattr(spec, "key", "?")

    def _log(msg):
        line = "pipe/%s %s" % (getattr(getattr(spec, "defn", None), "slug", key), msg)
        logs.append(line)
        sink = getattr(ctx, "log", None)
        if callable(sink):
            try:
                sink("[%s] %s" % (part, line))
            except Exception:  # noqa: BLE001
                pass

    def _fail(msg, **kw):
        _log("失败：%s" % msg)
        return RouteResult(part=part, key=key, route="pipe", status="error",
                           error=msg, logs=logs, seconds=round(time.time() - t0, 3), **kw)

    step_in = getattr(spec, "step", None)
    if not step_in or not Path(step_in).is_file():
        return _fail("模型 STEP 不存在：%s" % step_in)

    ok, why = available()
    if not ok:
        return _fail(why)

    try:
        dst_step = Path(spec.out_path("pipe"))
    except Exception:  # noqa: BLE001
        return _fail("spec.out_path('pipe') 失败")
    try:
        slug = spec.defn.slug
    except Exception:  # noqa: BLE001
        slug = str(key)
    work_dir = dst_step.parent / "_work" / slug
    work_dir.mkdir(parents=True, exist_ok=True)
    dst_step.parent.mkdir(parents=True, exist_ok=True)

    # 1) 主进程算分型方向（S3 selfref + S5 directions；缺一个就 auto）
    requested = _requested_direction(ctx)
    direction_option, label, rule, note = _direction_from_spec(spec, requested, log=_log)
    if note:
        _log("方向降级 auto：%s" % note)
    else:
        _log("分型方向 %s（%s）→ %s" % (label, rule, direction_option))

    # 2) 选项
    opts = _pipe_options(ctx, direction_option)
    _log("solidify 选项：%s" % json.dumps(opts, ensure_ascii=False))

    # 3) 执行（默认 headless；bridge/auto 失败降级）
    mode = _pipe_mode(ctx)
    report = None
    if mode in ("bridge", "auto"):
        try:
            report = _run_bridge(spec, str(work_dir), opts, logs)
        except Exception as exc:  # noqa: BLE001
            _log("GUI 桥不可用，降级 headless：%s" % exc)
            report = None
    if report is None:
        try:
            report = _run_headless(spec, str(work_dir), opts, ctx, logs)
        except Exception as exc:  # noqa: BLE001  （HeadlessError / OSError / 解析失败…）
            return _fail("FreeCADCmd 无头执行失败：%s" % exc)

    # 4) solid.step → spec.out_path("pipe")
    outputs = report.get("outputs") or {}
    produced = outputs.get("step")
    if not produced or not Path(produced).is_file():
        return _fail("solidify 未产出 STEP：%s" % produced)
    try:
        if Path(produced).resolve() != dst_step.resolve():
            shutil.copyfile(produced, dst_step)
    except OSError as exc:
        return _fail("STEP 复制失败：%s" % exc)
    dst_stl = None
    if outputs.get("stl") and Path(outputs["stl"]).is_file():
        dst_stl = dst_step.with_suffix(".stl")
        try:
            shutil.copyfile(outputs["stl"], dst_stl)
        except OSError:
            dst_stl = None

    # 5) 归一化
    report_json = work_dir / "report.json"
    if report_json.is_file():
        report["_report_json"] = str(report_json)
    result = _normalize(spec, report, dst_step=dst_step, dst_stl=dst_stl,
                        direction_option=direction_option, label=label, rule=rule,
                        logs=logs, seconds=time.time() - t0,
                        extra_artifacts={"work_dir": str(work_dir)})

    # 5.1) 主进程复检交付 STEP：FreeCAD 侧 report 只是被检对象的自述，
    #      checks.inspect_step 才是权威口径（4 面 / 1 实体 / 0 自由边 / 2 分型边）。
    #      FreeCAD 侧若只给 "ok"（例如 solidify 内部拿最长两条边当分型边），
    #      这里会以实际拓扑覆盖，并把不合规写进 problems。
    try:
        rec = checks.safe_inspect_step(dst_step) if Path(dst_step).is_file() else None
    except Exception as exc:  # noqa: BLE001 - 复检失败不掩盖已成型的产物
        rec = None
        _log("主进程复检异常：%s" % exc)
    if rec is not None:
        checks.merge_check(result, rec)
        errors = (rec.extra or {}).get("errors") or {}
        if errors:
            detail = "; ".join("%s: %s" % (k, v) for k, v in list(errors.items())[:3])
            result.status = "error"
            result.error = "交付 STEP 复检失败：%s" % detail
            _log(result.error)
        elif rec.valid is False:
            result.status = "error"
            result.error = "交付 STEP 不是有效实体（BRepCheck）"
            _log(result.error)
        result.problems = _problems(result)

    _log("完成：%d 面 / %d 边 / %d 实体 / valid=%s / 自由边 %s / 分型边 %s / 体积 %s mm3（偏差 %s%%）/ bbox_dev %s mm"
         % (result.faces or 0, result.edges or 0, result.solids or 0, result.valid,
            "?" if result.free_edges is None else result.free_edges,
            "?" if result.parting_edges is None else result.parting_edges,
            "%.0f" % result.volume_mm3 if result.volume_mm3 else "?",
            "?" if result.volume_error_pct is None else "%+.2f" % result.volume_error_pct,
            "?" if result.bbox_dev_mm is None else "%.2f" % result.bbox_dev_mm))
    result.logs = logs + list(report.get("logs") or [])
    result.seconds = round(time.time() - t0, 3)
    return result


__all__ = ["run", "available"]
