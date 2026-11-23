"""Assemble a solid from prepared half-section samples.

Kept outside geometry.py so the Giant section importer can reuse the exact
surface/cap/sew pipeline without duplicating the STEP strip extraction.
"""
from __future__ import annotations

import time
from typing import Sequence

import numpy as np

from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeWire,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
from OCP.BRepGProp import BRepGProp
from OCP.Geom import Geom_BSplineSurface
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.GProp import GProp_GProps
from OCP.TColStd import (
    TColStd_Array1OfInteger,
    TColStd_Array1OfReal,
    TColStd_HArray1OfReal,
)
from OCP.TColgp import TColgp_Array2OfPnt, TColgp_HArray1OfPnt
from OCP.TopAbs import TopAbs_EDGE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Pnt

from .geometry import (
    BuildOptions,
    BuildResult,
    MIN_SECTIONS,
    Section,
    ProgressCb,
    _build_half_surface,
    _make_cap,
    _parting_edges,
    _seam_max_angle,
    _sew_faces,
    _to_solid,
    bbox_size,
    free_edge_count,
    shape_bbox,
)


def _pnt_array(points: np.ndarray) -> TColgp_HArray1OfPnt:
    arr = TColgp_HArray1OfPnt(1, len(points))
    for i, p in enumerate(points):
        arr.SetValue(i + 1, gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
    return arr


def _closed_curve(points: np.ndarray):
    pts = np.asarray(points, dtype=float)
    if len(pts) > 2 and np.linalg.norm(pts[0] - pts[-1]) < 1e-9:
        pts = pts[:-1]
    # A periodic interpolation with an explicit uniform parameter table is
    # essential here: chord-length parameters depend on the outline shape, so
    # editing the first section would silently change the u knot vector used
    # for every other section and warp the whole surface.
    n = len(pts)
    params = TColStd_HArray1OfReal(1, n + 1)
    for i in range(1, n + 2):
        params.SetValue(i, (i - 1) / n)
    interp = GeomAPI_Interpolate(_pnt_array(pts), params, True, 1e-7)
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError("Giant 闭合截面曲线拟合失败")
    return interp.Curve()


def _closed_loop(sec: Section) -> np.ndarray:
    """Full cross-section in one consistent loop: right seam, top, left seam, bottom."""
    return np.vstack([sec.top_pts, sec.bottom_pts[1:-1]])


def _full_surface(sections: Sequence[Section]):
    curves = [_closed_curve(_closed_loop(sec)) for sec in sections]
    first = curves[0]
    u0 = float(first.FirstParameter())
    u1 = float(first.LastParameter())
    u_knots = [float(first.Knot(i)) for i in range(1, first.NbKnots() + 1)]
    u_mult = [int(first.Multiplicity(i)) for i in range(1, first.NbKnots() + 1)]
    n_u = int(first.NbPoles())
    if n_u < 4 or len(curves) < 4:
        raise RuntimeError("Giant 截面采样不足，无法构造全周曲面")

    v_curves = []
    for k in range(n_u):
        poles = np.array(
            [[c.Pole(k + 1).X(), c.Pole(k + 1).Y(), c.Pole(k + 1).Z()] for c in curves]
        )
        interp = GeomAPI_Interpolate(_pnt_array(poles), False, 1e-7)
        interp.Perform()
        if not interp.IsDone():
            raise RuntimeError("Giant 纵向截面曲线拟合失败")
        v_curves.append(interp.Curve())

    v_first = v_curves[0]
    v_knots = [float(v_first.Knot(i)) for i in range(1, v_first.NbKnots() + 1)]
    v_mult = [int(v_first.Multiplicity(i)) for i in range(1, v_first.NbKnots() + 1)]
    n_v = int(v_first.NbPoles())
    poles = TColgp_Array2OfPnt(1, n_u, 1, n_v)
    for k, curve in enumerate(v_curves):
        for j in range(1, n_v + 1):
            poles.SetValue(k + 1, j, curve.Pole(j))

    def real_array(values):
        arr = TColStd_Array1OfReal(1, len(values))
        for i, value in enumerate(values):
            arr.SetValue(i + 1, float(value))
        return arr

    def int_array(values):
        arr = TColStd_Array1OfInteger(1, len(values))
        for i, value in enumerate(values):
            arr.SetValue(i + 1, int(value))
        return arr

    surface = Geom_BSplineSurface(
        poles,
        real_array(u_knots),
        real_array(v_knots),
        int_array(u_mult),
        int_array(v_mult),
        3,
        3,
        True,
        False,
    )
    v0 = float(surface.VKnot(1))
    v1 = float(surface.VKnot(surface.NbVKnots()))
    return surface, u0, u1, v0, v1


def build_from_closed_sections(
    sections: Sequence[Section],
    options: BuildOptions,
    progress: ProgressCb | None = None,
) -> BuildResult:
    """Build one periodic surface through every full cross-section.

    The surface is split at the two symmetry seams, so both halves share the
    exact same seam curve.  This removes the duplicated parting edges produced
    by lofting the top and bottom halves independently.
    """
    options = options.clean()
    started = time.time()
    warnings: list[str] = []

    def report(value: float, text: str) -> None:
        if progress:
            progress(value, text)

    if len(sections) < MIN_SECTIONS:
        raise RuntimeError("截面数量不足，无法重建")

    report(0.55, "用 Giant 实测截面拟合全周 B 样条曲面")
    # The closing cap must be planar.  Measurement/resampling can leave a
    # sub-micron normal deviation at the end rings; OCC's planar check is
    # stricter than that, so project the first and last loops exactly onto
    # their own best-fit planes before lofting.
    for sec in (sections[0], sections[-1]):
        loop = np.vstack([sec.top_pts, sec.bottom_pts])
        center = loop.mean(axis=0)
        _, _, vectors = np.linalg.svd(loop - center, full_matrices=False)
        normal = vectors[-1]
        sec.top_pts = sec.top_pts - np.outer((sec.top_pts - center) @ normal, normal)
        sec.bottom_pts = sec.bottom_pts - np.outer((sec.bottom_pts - center) @ normal, normal)
        sec.normal = normal
    surface, u0, u1, v0, v1 = _full_surface(sections)
    u_mid = 0.5 * (u0 + u1)
    face_a = BRepBuilderAPI_MakeFace(surface, u0, u_mid, v0, v1, 1e-6).Face()
    face_b = BRepBuilderAPI_MakeFace(surface, u_mid, u1, v0, v1, 1e-6).Face()
    if face_a.IsNull() or face_b.IsNull():
        raise RuntimeError("全周曲面拆分失败")

    report(0.72, "构造平面端盖并缝合为实体")
    cap_faces = []
    for v in (v0, v1):
        iso = surface.VIso(v)
        edge_a = BRepBuilderAPI_MakeEdge(iso, u0, u_mid).Edge()
        edge_b = BRepBuilderAPI_MakeEdge(iso, u_mid, u1).Edge()
        wire = BRepBuilderAPI_MakeWire(edge_a, edge_b)
        if not wire.IsDone():
            wire = BRepBuilderAPI_MakeWire(edge_b, edge_a)
        if not wire.IsDone():
            raise RuntimeError("端盖轮廓线无法闭合")
        cap = BRepBuilderAPI_MakeFace(wire.Wire(), True).Face()
        if cap.IsNull():
            raise RuntimeError("端盖面构造失败")
        cap_faces.append(cap)

    tolerance = options.sew_tolerance
    solid = None
    for attempt in range(3):
        try:
            sewed = _sew_faces([face_a, face_b, *cap_faces], tolerance)
            candidate = _to_solid(sewed)
            if BRepCheck_Analyzer(candidate).IsValid() and free_edge_count(candidate) == 0:
                solid = candidate
                break
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"缝合尝试 {attempt + 1} 失败: {exc}")
        tolerance *= 10.0
    if solid is None:
        raise RuntimeError("无法把全周曲面缝合为闭合实体")
    options.sew_tolerance = tolerance

    report(0.88, "检查实体有效性")
    if not BRepCheck_Analyzer(solid).IsValid():
        raise RuntimeError("生成的实体未通过 OCC 有效性检查")
    if free_edge_count(solid) > 0:
        raise RuntimeError("缝合后仍存在自由边，实体未闭合")

    bbox = shape_bbox(solid)
    extent = float(max(bbox_size(bbox)))
    parting = _parting_edges(solid, extent)
    if len(parting) != 2:
        warnings.append(f"检测到 {len(parting)} 条纵向分型边，期望 2 条")

    report(0.96, "生成预览与输出文件")
    return BuildResult(
        solid=solid,
        sections=list(sections),
        top_face=face_a,
        bottom_face=face_b,
        cap_faces=cap_faces,
        parting_edges=parting,
        build_seconds=time.time() - started,
        u_poles=int(surface.NbUPoles()),
        v_poles=int(surface.NbVPoles()),
        sew_tolerance=options.sew_tolerance,
        seam_max_angle_deg=0.0,
        parting_axis=sections[0].axis_name,
        symmetry_scores=sections[0].symmetry_scores,
        repair_mode="GIANT",
        warnings=warnings,
    )


def build_from_prepared_sections(
    sections: Sequence[Section],
    options: BuildOptions,
    progress: ProgressCb | None = None,
) -> BuildResult:
    options = options.clean()
    started = time.time()
    warnings: list[str] = []

    def report(value: float, text: str) -> None:
        if progress:
            progress(value, text)

    if len(sections) < MIN_SECTIONS:
        raise RuntimeError("截面数量不足，无法重建")

    centers = np.array([s.center for s in sections])
    distances = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))]
    if distances[-1] < 1e-9:
        distances = np.linspace(0.0, 1.0, len(sections))
    v_params = distances / distances[-1]

    report(0.55, "拟合两侧光滑 B 样条曲面")
    top_surface = _build_half_surface(sections, "top", v_params)
    bottom_surface = _build_half_surface(sections, "bottom", v_params)
    top_face = BRepBuilderAPI_MakeFace(top_surface, 1e-6).Face()
    bottom_face = BRepBuilderAPI_MakeFace(bottom_surface, 1e-6).Face()
    if top_face.IsNull() or bottom_face.IsNull():
        raise RuntimeError("侧面曲面构造失败")

    report(0.72, "构造平面端盖并缝合为实体")
    v_start = float(top_surface.VKnot(1))
    v_end = float(top_surface.VKnot(top_surface.NbVKnots()))
    cap_start = _make_cap(top_surface, bottom_surface, v_start)
    cap_end = _make_cap(top_surface, bottom_surface, v_end)

    tolerance = options.sew_tolerance
    sewed = None
    solid = None
    for attempt in range(3):
        try:
            sewed = _sew_faces([top_face, bottom_face, cap_start, cap_end], tolerance)
            solid = _to_solid(sewed)
            if BRepCheck_Analyzer(solid).IsValid() and free_edge_count(solid) == 0:
                break
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"缝合尝试 {attempt + 1} 失败: {exc}")
        tolerance *= 10.0
    if solid is None:
        raise RuntimeError("无法把曲面缝合为闭合实体")
    options.sew_tolerance = tolerance

    report(0.88, "检查实体有效性")
    if not BRepCheck_Analyzer(solid).IsValid():
        raise RuntimeError("生成的实体未通过 OCC 有效性检查")
    if free_edge_count(solid) > 0:
        raise RuntimeError("缝合后仍存在自由边，实体未闭合")

    bbox = shape_bbox(solid)
    extent = float(max(bbox_size(bbox)))
    parting = _parting_edges(solid, extent)
    seam_angle = _seam_max_angle(top_surface, bottom_surface)
    if len(parting) != 2:
        warnings.append(f"检测到 {len(parting)} 条纵向分型边，期望 2 条")

    report(0.96, "生成预览与输出文件")
    return BuildResult(
        solid=solid,
        sections=list(sections),
        top_face=top_face,
        bottom_face=bottom_face,
        cap_faces=[cap_start, cap_end],
        parting_edges=parting,
        build_seconds=time.time() - started,
        u_poles=int(top_surface.NbUPoles()),
        v_poles=int(top_surface.NbVPoles()),
        sew_tolerance=options.sew_tolerance,
        seam_max_angle_deg=seam_angle,
        parting_axis=sections[0].axis_name,
        symmetry_scores=sections[0].symmetry_scores,
        repair_mode="GIANT",
        warnings=warnings,
    )


def fillet_port_edges(shape, radius: float):
    """Round the two end-cap boundaries (both ports) with a constant radius."""
    if radius <= 0:
        return shape, False, "未倒圆"
    bbox = shape_bbox(shape)
    extent = float(max(bbox_size(bbox)))
    short = []
    seen = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge_s(explorer.Current())
        if not any(edge.IsSame(other) for other in seen):
            seen.append(edge)
            props = GProp_GProps()
            BRepGProp.LinearProperties_s(edge, props)
            if props.Mass() < 0.3 * extent:
                short.append(edge)
        explorer.Next()
    if not short:
        return shape, False, "未找到端口边"
    try:
        fillet = BRepFilletAPI_MakeFillet(shape)
        for edge in short:
            fillet.Add(radius, edge)
        fillet.Build()
        if not fillet.IsDone():
            return shape, False, "R2 倒圆失败，已保留未倒圆实体"
        rounded = fillet.Shape()
        if not BRepCheck_Analyzer(rounded).IsValid():
            return shape, False, "倒圆结果无效，已保留未倒圆实体"
        return rounded, True, f"两端 R{radius:g} 倒圆"
    except Exception as exc:  # noqa: BLE001
        return shape, False, f"倒圆失败：{exc}"
