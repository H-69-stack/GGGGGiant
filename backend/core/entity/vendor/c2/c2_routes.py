"""C2 的两条重建路线。

reference 路线（情况 121）
    read_step(参考)
      → extract_sections(自动检测对称轴)
      → prepare_sections(每个截面沿对称方向取左右极点当分型点)
      → build_from_closed_sections(一张周期 B 样条穿过所有截面环，从两条极点缝切成 2 片
        + 2 个平面端盖)
      → 4 面 / 1 实体 / 2 条共享分型边 / 0 自由边 / valid

c1 路线（情况二）
    <管件>_measured.json
      → load_giant_sections(..., smooth_passes=2, keep_extrapolated=True,
        keep_all_extrapolated=False)   # 与官方 giant_cli 同参数
      → build_from_closed_sections → 同一个 4 面实体规范

两条路线都不改输入数据：C2 只把“已经正确的截面形状”转成 4 面模具实体。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.GeomAbs import GeomAbs_Plane
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from . import c2_config as cfg
from ..pipe_smoother.assemble import build_from_closed_sections
from ..pipe_smoother.geometry import (
    BuildOptions,
    _parting_edges,
    bbox_size,
    count_subshapes,
    extract_sections,
    free_edge_count,
    prepare_sections,
    read_step,
    sample_edge,
    shape_bbox,
    write_step,
)
from ..pipe_smoother.giant_sections import load_giant_sections

AXES = "XYZ"


# ---------------------------------------------------------------- 实体体检


def _round(values, nd: int = 4) -> list[float]:
    return [round(float(v), nd) for v in values]


def _planar_faces(shape) -> int:
    n = 0
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face_s(ex.Current())
        try:
            if BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane:
                n += 1
        except Exception:  # noqa: BLE001 - 个别面取不到曲面就跳过
            pass
        ex.Next()
    return n


def parting_dir_of_shape(shape, parting_edges) -> str:
    """从成对分型边的位置推分型方向（Z+/Z- 这种），不依赖任何标签。

    两条纵向分型边是左右镜像的一对，把两条边各自的中点一相减，差向量就落在
    “对称方向”上，正好就是分型线所在的那一对方向。
    """
    if len(parting_edges) != 2:
        return "?"
    try:
        a = sample_edge(parting_edges[0], 41)
        b = sample_edge(parting_edges[1], 41)
    except Exception:  # noqa: BLE001 - 个别边取不到参数曲线就放弃
        return "?"
    diff = a.mean(axis=0) - b.mean(axis=0)
    if float(np.linalg.norm(diff)) < 1e-9:
        return "?"
    axis = classify_dir(diff)
    return f"{axis[0]}+/{axis[0]}-"


def inspect_shape(shape) -> dict:
    """4 面规范的体检指标：面数、实体数、自由边、分型边、有效性、体积、包围盒。"""
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    bbox = shape_bbox(shape)
    extent = float(max(bbox_size(bbox)))
    parting = _parting_edges(shape, extent) if extent > 0 else []
    return {
        "faces": count_subshapes(shape, TopAbs_FACE),
        "solids": count_subshapes(shape, TopAbs_SOLID),
        "planar_faces": _planar_faces(shape),
        "free_edges": free_edge_count(shape),
        "parting_edges": len(parting),
        "parting_dir": parting_dir_of_shape(shape, parting),
        "valid": bool(BRepCheck_Analyzer(shape).IsValid()),
        "volume_mm3": round(float(props.Mass()), 3),
        "bbox": _round(bbox),
    }


def inspect_step(path) -> dict:
    p = Path(path)
    shape = read_step(p)
    info = inspect_shape(shape)
    info["path"] = str(p)
    info["bytes"] = p.stat().st_size
    info["sha256"] = cfg.sha256_file(p).lower()
    return info


def validate_4face(info: dict) -> list[str]:
    """4 面模具实体的硬性校验；返回问题清单（空 = 通过）。"""
    issues: list[str] = []
    if info.get("faces") != 4:
        issues.append(f"面数 {info.get('faces')} ≠ 4")
    if info.get("solids") != 1:
        issues.append(f"实体数 {info.get('solids')} ≠ 1")
    if info.get("free_edges") != 0:
        issues.append(f"自由边 {info.get('free_edges')} ≠ 0")
    if info.get("parting_edges") != 2:
        issues.append(f"共享分型边 {info.get('parting_edges')} ≠ 2")
    if info.get("planar_faces", 0) < 2:
        issues.append("平面端盖少于 2 个")
    if not info.get("valid"):
        issues.append("OCC 有效性检查未通过")
    return issues


# ---------------------------------------------------------------- 方向与保形


def classify_dir(vector) -> str:
    """把一个方向向量归一成 Y+/Y- 这种带符号的全局轴。"""
    v = np.asarray(vector, dtype=float).ravel()
    if v.size < 3 or not np.any(np.isfinite(v)):
        return "?"
    idx = int(np.argmax(np.abs(v[:3])))
    return f"{AXES[idx]}{'+' if v[idx] >= 0 else '-'}"


def _polygon_area(points: np.ndarray, normal) -> float:
    p = np.asarray(points, dtype=float)
    if len(p) < 4:
        return 0.0
    center = p.mean(axis=0)
    q = p - center
    n = np.asarray(normal, dtype=float).ravel()[:3]
    nn = float(np.linalg.norm(n))
    if nn < 1e-12:
        return 0.0
    n = n / nn
    cross = np.cross(q[:-1], q[1:])          # 每条边相对中心的叉积向量
    return float(abs(0.5 * float(cross.sum(axis=0) @ n)))


def ring_resample_error(sections) -> dict:
    """保形检查：分型重采样后的闭合环面积 vs 原始测量环面积。

    build_from_closed_sections 用 GeomAPI_Interpolate(周期, 1e-7) 穿过每一圈采样点，
    所以这个面积差就是“4 面实体相对参考环的截面偏差”的直接体现。
    """
    worst, total, count = 0.0, 0.0, 0
    for sec in sections:
        raw = np.asarray(sec.points, dtype=float)
        loop = np.vstack([sec.top_pts, sec.bottom_pts[1:-1]])
        a_raw = _polygon_area(raw, sec.normal)
        a_loop = _polygon_area(loop, sec.normal)
        if a_raw <= 1e-9:
            continue
        err = abs(a_loop - a_raw) / a_raw
        worst = max(worst, err)
        total += err
        count += 1
    return {
        "ring_area_rel_err_max": round(worst, 8),
        "ring_area_rel_err_mean": round(total / count, 8) if count else 0.0,
        "ring_area_checked": count,
    }


def _delta_pct(value: float, base: float) -> float | None:
    if not base:
        return None
    return round((value - base) / base * 100.0, 4)


# ---------------------------------------------------------------- 质量门禁


def gate_reference(entry: dict, gate: dict | None = None, deep: bool = True):
    """校验清单里登记的 121 参考模型是否可以采信。

    返回 (gate_result, shape_or_None)。shape 已经读出来了就顺手交给 build 复用，
    省掉一次 1~5 s 的 STEP 解析。
    """
    gate = {**cfg.DEFAULT_GATE, **(gate or {})}
    issues: list[str] = []
    warnings: list[str] = []
    live: dict = {}

    path = entry.get("reference")
    if not path:
        return ({"ok": False, "issues": ["清单未登记参考模型"], "warnings": [], "live": {}}, None)
    p = Path(path)
    if not p.is_file():
        return ({"ok": False, "issues": [f"参考模型文件不存在: {path}"], "warnings": [], "live": {}}, None)

    live["bytes"] = p.stat().st_size
    live["sha256"] = cfg.sha256_file(p).lower()
    declared_sha = (entry.get("sha256") or "").lower()
    if gate["require_sha256"] and not declared_sha:
        issues.append("清单未登记 sha256，参考不可信（先跑 c2_build.py --register）")
    elif declared_sha and live["sha256"] != declared_sha:
        issues.append("sha256 与清单不一致，参考模型被替换过")

    if not deep:
        return ({"ok": not issues, "issues": issues, "warnings": warnings, "live": live}, None)

    shape = read_step(p)
    info = inspect_shape(shape)
    live.update(info)

    declared_faces = entry.get("faces")
    if declared_faces is not None and abs(int(info["faces"]) - int(declared_faces)) > int(gate["face_tolerance"]):
        issues.append(f"面数 {info['faces']} 与清单登记 {declared_faces} 不一致")
    if gate["require_valid"] and not info["valid"]:
        issues.append("参考模型未通过 OCC 有效性检查")
    if gate["require_single_solid"] and info["solids"] != 1:
        issues.append(f"参考模型实体数 {info['solids']} ≠ 1")
    if info["free_edges"] > int(gate["free_edge_max"]):
        issues.append(f"参考模型自由边 {info['free_edges']} > {gate['free_edge_max']}")
    declared_volume = entry.get("volume_mm3")
    if declared_volume:
        drift = abs(info["volume_mm3"] - float(declared_volume)) / float(declared_volume) * 100.0
        live["volume_drift_pct"] = round(drift, 4)
        if drift > float(gate["volume_tolerance_pct"]):
            issues.append(
                f"参考模型体积漂移 {drift:.3f}% 超过门禁 {gate['volume_tolerance_pct']}%"
            )

    return ({"ok": not issues, "issues": issues, "warnings": warnings, "live": live}, shape)


# ---------------------------------------------------------------- 121 参考路线


def build_reference(entry: dict, out_path, half_samples: int = cfg.HALF_SAMPLES, shape=None) -> dict:
    """参考模型 → 4 面 Solid（保形）。"""
    started = time.time()
    path = Path(entry["reference"])
    t0 = time.time()
    if shape is None:
        shape = read_step(path)
    read_seconds = time.time() - t0

    t0 = time.time()
    sections = extract_sections(shape, parting_axis="auto")
    extract_seconds = time.time() - t0
    if len(sections) < 4:
        raise RuntimeError(f"参考模型只识别到 {len(sections)} 个截面环，无法重建")

    axis = sections[0].axis_name or "?"
    scores = {k: round(float(v), 4) for k, v in (sections[0].symmetry_scores or {}).items()}

    prepare_sections(sections, half_samples)
    fidelity = ring_resample_error(sections)

    t0 = time.time()
    result = build_from_closed_sections(sections, BuildOptions(half_samples=half_samples))
    build_seconds = time.time() - t0

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_step(result.solid, str(out))

    info = inspect_shape(result.solid)
    info.update(fidelity)
    issues = validate_4face(info)
    info.update(
        {
            "route": "reference",
            "path": str(out),
            "reference": str(path),
            "reference_faces": int(count_subshapes(shape, TopAbs_FACE)),
            "rings": len(sections),
            "symmetry_axis": axis,
            "symmetry_scores": scores,
            "parting_axis": f"{axis}+/{axis}-",
            "u_poles": getattr(result, "u_poles", 0),
            "v_poles": getattr(result, "v_poles", 0),
            "sew_tolerance": round(float(getattr(result, "sew_tolerance", 0.0)), 9),
            "warnings": list(getattr(result, "warnings", [])),
            "issues": issues,
            "ok": not issues,
            "bytes": out.stat().st_size,
            "sha256": cfg.sha256_file(out).lower(),
            "seconds": {
                "read": round(read_seconds, 2),
                "extract": round(extract_seconds, 2),
                "build": round(build_seconds, 2),
                "total": round(time.time() - started, 2),
            },
        }
    )
    return info


# ---------------------------------------------------------------- C1 路线


def build_c1(measured_path, out_path, half_samples: int = cfg.HALF_SAMPLES) -> dict:
    """raw C1 外推截面 → 4 面 Solid（情况二，C2 的默认路线）。"""
    started = time.time()
    src = Path(measured_path)
    if not src.is_file():
        raise RuntimeError(f"raw 缓存不存在: {src}")

    t0 = time.time()
    sections = load_giant_sections(
        src,
        step_mm=2.0,
        half_samples=half_samples,
        smooth_passes=2,
        keep_extrapolated=True,
        keep_all_extrapolated=False,
    )
    load_seconds = time.time() - t0

    t0 = time.time()
    result = build_from_closed_sections(sections, BuildOptions(half_samples=half_samples))
    build_seconds = time.time() - t0

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_step(result.solid, str(out))

    info = inspect_shape(result.solid)
    info.update(ring_resample_error(sections))
    issues = validate_4face(info)
    side = getattr(sections[0], "side", None)
    side_axis = classify_dir(side)[0] if side is not None else "?"
    info.update(
        {
            "route": "c1",
            "path": str(out),
            "measured": str(src),
            "measured_bytes": src.stat().st_size,
            "sections": len(sections),
            "parting_axis": f"{side_axis}+/{side_axis}-",
            "u_poles": getattr(result, "u_poles", 0),
            "v_poles": getattr(result, "v_poles", 0),
            "sew_tolerance": round(float(getattr(result, "sew_tolerance", 0.0)), 9),
            "warnings": list(getattr(result, "warnings", [])),
            "issues": issues,
            "ok": not issues,
            "bytes": out.stat().st_size,
            "sha256": cfg.sha256_file(out).lower(),
            "seconds": {
                "load": round(load_seconds, 2),
                "build": round(build_seconds, 2),
                "total": round(time.time() - started, 2),
            },
        }
    )
    return info


# ---------------------------------------------------------------- 交叉校验


def cross_check(adopted: dict, other: dict | None, label: str) -> dict | None:
    """用另一条路线（或重建件）的体积差给“保形”背书。"""
    if not other or not other.get("volume_mm3"):
        return None
    return {
        "with": label,
        "path": other.get("path"),
        "sections": other.get("sections") or other.get("rings"),
        "volume_mm3": other.get("volume_mm3"),
        "delta_pct": _delta_pct(adopted.get("volume_mm3", 0.0), other["volume_mm3"]),
    }


def cross_check_file(adopted: dict, path, label: str) -> dict | None:
    """拿磁盘上已有的另一条路线成品做体积比对（不重建，只量体积）。"""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        info = inspect_step(p)
    except Exception:  # noqa: BLE001 - 读不动就不做交叉校验
        return None
    return cross_check(adopted, info, label)
