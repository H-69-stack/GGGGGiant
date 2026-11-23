# -*- coding: utf-8 -*-
"""Processing entry point used by both the GUI bridge and FreeCADCmd runner."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import Part
from FreeCAD import Vector

from . import geometry as geo

_HEX_PREFIX = re.compile(r"^[0-9a-fA-F]{8}_")


def display_name(file_name):
    """Hide the upload's 8-hex prefix in reports and FreeCAD document names."""
    return _HEX_PREFIX.sub("", os.path.basename(file_name or ""))

LOG = []


def _log(msg):
    line = "%s  %s" % (time.strftime("%H:%M:%S"), msg)
    LOG.append(line)
    try:
        print(line, flush=True)
    except Exception:
        try:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass


def resolve_direction(option, rings):
    """Return (Vector, label). option: auto / X / Y / Z / [x,y,z]."""
    if isinstance(option, (list, tuple)) and len(option) == 3:
        v = Vector(float(option[0]), float(option[1]), float(option[2]))
        v.normalize()
        return v, "custom(%.3f,%.3f,%.3f)" % (v.x, v.y, v.z)
    opt = str(option or "auto").strip().upper()
    if opt in ("X", "Y", "Z"):
        v = Vector(0, 0, 0)
        v["XYZ".index(opt)] = 1.0
        return v, "%s（手动）" % opt
    scores = geo.auto_direction_scores(rings)
    axis = geo.pick_symmetry_axis(scores)
    _log("自动对称评分：X=%.5f  Y=%.5f  Z=%.5f -> %s" % (
        scores[0], scores[1], scores[2], "XYZ"[axis]))
    v = Vector(0, 0, 0)
    v[axis] = 1.0
    return v, "auto->%s（镜像对称轴）" % "XYZ"[axis]


def estimate_volume(rings):
    """Prismatoid estimate from the section rings (robust against overlapping bands)."""
    areas = [Part.Face(Part.Wire([e])).Area for e in rings]
    total = 0.0
    for i in range(len(rings) - 1):
        d = (rings[i + 1].CenterOfMass - rings[i].CenterOfMass).Length
        total += (areas[i] + areas[i + 1]) * 0.5 * d
    return total


def _bbox_list(bb):
    return [float(bb.XMin), float(bb.YMin), float(bb.ZMin),
            float(bb.XMax), float(bb.YMax), float(bb.ZMax)]


def _discretize(edge, count=80):
    fp, lp = edge.FirstParameter, edge.LastParameter
    pts = []
    for i in range(count + 1):
        p = edge.valueAt(fp + (lp - fp) * i / count)
        pts.append([round(float(p.x), 4), round(float(p.y), 4), round(float(p.z), 4)])
    return pts


def _parting_edges(solid, direction):
    length = max(solid.BoundBox.XLength, solid.BoundBox.YLength, solid.BoundBox.ZLength)
    edges = [e for e in solid.Edges if e.BoundBox.DiagonalLength > 0.6 * length]
    d = Vector(direction)

    def side(e):
        return e.CenterOfMass.dot(d)

    edges.sort(key=side, reverse=True)
    return edges[:2]


def process_file(step_path, out_dir, options=None):
    """Build the smooth solid, export STEP/STL, and return a report.

    The returned dict also carries ``_solid`` and ``_edge_polylines`` for the GUI
    bridge (stripped before XML-RPC serialisation).
    """
    LOG.clear()
    opts = dict(options or {})
    half_points = int(opts.get("half_points", 96))
    max_sections = opts.get("max_sections", None)
    if str(max_sections).lower() in ("auto", "none", ""):
        max_sections = None
    else:
        max_sections = int(max_sections)
    export_stl = bool(opts.get("export_stl", True))

    t0 = time.time()
    _log("读取 STEP：%s" % display_name(os.path.basename(step_path)))
    shape = geo.load_shape(step_path)
    _log("源模型：%d 面 / %d 边 / %d 实体，体积 %.0f mm3" % (
        len(shape.Faces), len(shape.Edges), len(shape.Solids), shape.Volume))

    rings, spine = geo.section_edges(shape)
    _log("识别截面环 %d 个，主轴线 %s" % (len(rings), "XYZ"[spine]))
    direction, direction_label = resolve_direction(opts.get("direction", "auto"), rings)
    _log("分型方向：%s" % direction_label)

    source_vol = estimate_volume(rings)
    bbox = _bbox_list(shape.BoundBox)
    _log("源体积估计（截面棱柱法）：%.0f mm3" % source_vol)

    solid, info = geo.build_solid(rings, direction, half_points, source_vol,
                                  max_sections=max_sections, source_bbox=bbox)
    _log("光滑实体完成：%d 面 / %d 边 / 有效=%s / 体积 %.0f mm3（偏差 %+.2f%%）" % (
        info["faces"], info["edges"], info["valid"], info["volume"],
        info["volume_error_pct"] or 0.0))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(step_path).stem
    step_out = out / "solid.step"
    solid.exportStep(str(step_out))
    if not step_out.exists():
        raise RuntimeError("STEP 导出失败：%s" % step_out)
    _log("STEP 已导出：%s (%.1f MB)" % (step_out, step_out.stat().st_size / 1048576.0))

    stl_out = None
    if export_stl:
        try:
            import MeshPart
            mesh = MeshPart.meshFromShape(Shape=solid, LinearDeflection=0.25,
                                          AngularDeflection=0.5, Relative=False)
            stl_out = out / "solid.stl"
            mesh.write(str(stl_out))
            _log("STL 已导出：%s (%d 三角面)" % (stl_out, mesh.CountFacets))
        except Exception as exc:
            _log("STL 导出跳过：%s" % exc)
            stl_out = None

    edges = _parting_edges(solid, direction)
    polylines = [_discretize(e, 80) for e in edges]
    _log("分型边：%d 条实体棱线（%s）" % (len(edges), " / ".join("%.1fmm" % e.Length for e in edges)))

    bb = solid.BoundBox
    dev = max(abs(bb.XMin - bbox[0]), abs(bb.YMin - bbox[1]), abs(bb.ZMin - bbox[2]),
              abs(bb.XMax - bbox[3]), abs(bb.YMax - bbox[4]), abs(bb.ZMax - bbox[5]))
    report = {
        "status": "ok",
        "input": str(step_path),
        "outputs": {"step": str(step_out), "stl": str(stl_out) if stl_out else None},
        "source": {
            "name": display_name(os.path.basename(step_path)),
            "file_name": os.path.basename(step_path),
            "bytes": os.path.getsize(step_path),
            "faces": len(shape.Faces),
            "edges": len(shape.Edges),
            "solids": len(shape.Solids),
            "volume": float(shape.Volume),
            "volume_est": float(source_vol),
            "bbox": bbox,
            "sections": len(rings),
        },
        "result": dict(info, bbox=_bbox_list(bb), bbox_dev=float(dev),
                       direction_label=direction_label,
                       parting_edges=len(edges),
                       edge_lengths=[float(e.Length) for e in edges]),
        "options": {
            "direction": opts.get("direction", "auto"),
            "half_points": half_points,
            "max_sections": max_sections if max_sections is not None else "auto",
            "export_stl": export_stl,
        },
        "logs": list(LOG),
        "seconds": round(time.time() - t0, 2),
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"report": report, "solid": solid, "edges": polylines, "_edge_shapes": edges}

