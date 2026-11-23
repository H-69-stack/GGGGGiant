# -*- coding: utf-8 -*-
"""在 FreeCADCmd 进程内执行：读 PIPESOLID_JOB → 跑 solidify → 写结果 JSON。

本文件由主进程用 ``freecadcmd.exe -c "exec(compile(open(<本文件>...)))"`` 执行，
**禁止** import ``backend`` 下的任何模块（FreeCAD 自带 Python 看不到主进程环境）。

改造点：结果 json 里额外补一份 ``shape_stats``（solids / free_edges / closed /
bbox / volume）。solidify.py 的算法不动 —— 这里只是对返回的 ``_solid`` 做只读统计，
让 RouteResult 的 ``solids`` / ``free_edges`` 是实测值而不是推断值。
"""
import json
import os
import sys
import traceback

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _optimal_bbox(shape):
    """OCC 的精确包围盒（BndLib::AddOptimal）。

    注意：``Shape.BoundBox`` 对 BSpline 面是**保守值**（用控制点算），会虚报几百微米
    到几毫米的「过冲」；实测 24TK_1324-1 成型件 BoundBox 偏差 3.27 mm，而
    ``optimalBoundingBox()`` 只有 0.003–0.026 mm。所以额外记一份精确值，
    主进程优先用它做 bbox_dev，免得把度量伪影当成算法缺陷。
    """
    for args in ((1e-4, False), (False,)):
        try:
            bb = shape.optimalBoundingBox(*args)
        except Exception:
            continue
        try:
            return [float(bb.XMin), float(bb.YMin), float(bb.ZMin),
                    float(bb.XMax), float(bb.YMax), float(bb.ZMax)]
        except Exception:
            continue
    return None


def _dev(a, b):
    if not a or not b or len(a) != 6 or len(b) != 6:
        return None
    return float(max(abs(x - y) for x, y in zip(a, b)))


def _shape_stats(shape):
    """只读统计：面/边/实体/自由边/是否闭合/包围盒/体积。"""
    from collections import Counter

    try:
        faces = len(shape.Faces)
        edges = len(shape.Edges)
        solids = len(shape.Solids)
    except Exception:
        return None
    used = Counter()
    try:
        for face in shape.Faces:
            used.update(edge.hashCode() for edge in face.Edges)
        free_edges = sum(1 for count in used.values() if count == 1)
    except Exception:
        free_edges = None
    stats = {"faces": faces, "edges": edges, "solids": solids, "free_edges": free_edges}
    try:
        stats["closed"] = bool(shape.isClosed())
    except Exception:
        stats["closed"] = None
    try:
        bb = shape.BoundBox
        stats["bbox"] = [float(bb.XMin), float(bb.YMin), float(bb.ZMin),
                         float(bb.XMax), float(bb.YMax), float(bb.ZMax)]
    except Exception:
        stats["bbox"] = None
    try:
        stats["volume"] = float(shape.Volume)
    except Exception:
        stats["volume"] = None
    opt = _optimal_bbox(shape)
    if opt:
        stats["bbox_optimal"] = opt
    return stats


job_path = os.environ["PIPESOLID_JOB"]
with open(job_path, encoding="utf-8") as fh:
    job = json.load(fh)
result_path = job["result_json"]
try:
    sys.path.insert(0, job["addon_parent"])
    from PipeSolidBridge import solidify

    out = solidify.process_file(job["input"], job["output_dir"], job.get("options") or {})
    report = out["report"]
    report["preview_edges"] = out.get("edges") or []
    report["opened_in_freecad"] = False
    stats = _shape_stats(out.get("solid"))
    if stats:
        src_bbox = (report.get("source") or {}).get("bbox")
        dev_opt = _dev(stats.get("bbox_optimal"), src_bbox)
        if dev_opt is not None:
            stats["bbox_dev_optimal"] = dev_opt
        dev_box = _dev(stats.get("bbox"), src_bbox)
        if dev_box is not None:
            stats["bbox_dev_boundbox"] = dev_box
        report["shape_stats"] = stats
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False)
except BaseException as exc:  # noqa: BLE001
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump({"status": "error", "message": str(exc),
                   "traceback": traceback.format_exc()}, fh, ensure_ascii=False)
    traceback.print_exc()
    sys.exit(1)
