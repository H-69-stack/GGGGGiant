"""Core geometry reconstruction for sectioned tube/pipe STEP models.

The input models are single solids whose lateral surface is made from many
separate B-spline strips, one strip between each pair of cross-sections.
The strips meet with G0 (position) continuity, so the model is full of
"section edges" even though the nominal shape is smooth.

This module rebuilds the shape as a clean solid:

1. find the unique planar closed section loops and order them along the spine;
2. find the two local-Y extrema of every loop (the parting points);
3. split every loop at those points into an upper and a lower half;
4. fit a C1/C2 cubic B-spline through each half and loft the halves along the
   spine with a cubic B-spline (the result passes through every section);
5. sew the two lateral faces and two planar end caps into a single solid.

Because the upper and lower halves are separate faces, their shared curves
remain real edges of the solid.  The surface is tangent-continuous across
those edges, so the edges are parting lines rather than creases.
"""
from __future__ import annotations

import copy
import json
import math
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import numpy as np

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Sewing,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Geom import Geom_BSplineSurface, Geom_BezierCurve
from OCP.GeomLProp import GeomLProp_SLProps
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.GeomConvert import GeomConvert_CompCurveToBSplineCurve
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import (
    STEPControl_Reader,
    STEPControl_StepModelType,
    STEPControl_Writer,
)
from OCP.ShapeFix import ShapeFix_Solid
from OCP.StlAPI import StlAPI_Writer
from OCP.TColgp import (
    TColgp_Array1OfPnt,
    TColgp_Array2OfPnt,
    TColgp_HArray1OfPnt,
)
from OCP.TColStd import (
    TColStd_Array1OfInteger,
    TColStd_Array1OfReal,
    TColStd_HArray1OfReal,
)
from OCP.TopAbs import (
    TopAbs_EDGE,
    TopAbs_FACE,
    TopAbs_REVERSED,
    TopAbs_SHELL,
    TopAbs_SOLID,
)
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from OCP.gp import gp_Pnt, gp_Vec

ProgressCb = Callable[[float, str], None]

CURVE_TOL = 1e-7
MIN_SECTIONS = 4


# ---------------------------------------------------------------------------
# small OCC helpers
# ---------------------------------------------------------------------------


def pnt3(obj) -> np.ndarray:
    if hasattr(obj, "X") and hasattr(obj, "Y") and hasattr(obj, "Z"):
        return np.array([obj.X(), obj.Y(), obj.Z()], dtype=float)
    return np.asarray(obj, dtype=float)


def shape_bbox(shape) -> tuple[float, float, float, float, float, float]:
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    return tuple(float(v) for v in box.Get())


def bbox_size(bb: Sequence[float]) -> np.ndarray:
    return np.array([bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]], dtype=float)


def shape_length(shape) -> float:
    bb = shape_bbox(shape)
    return float(np.linalg.norm(bbox_size(bb)))


def edge_length(edge) -> float:
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    return float(props.Mass())


def count_subshapes(shape, kind) -> int:
    exp = TopExp_Explorer(shape, kind)
    n = 0
    while exp.More():
        n += 1
        exp.Next()
    return n


def face_edge_map(shape):
    mapping = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, mapping)
    return mapping


def free_edge_count(shape) -> int:
    mapping = face_edge_map(shape)
    n = 0
    for i in range(1, mapping.Extent() + 1):
        if mapping.FindFromIndex(i).Extent() == 1:
            n += 1
    return n


def sample_edge(edge, n: int = 240) -> np.ndarray:
    curve = BRepAdaptor_Curve(edge)
    t0, t1 = curve.FirstParameter(), curve.LastParameter()
    return np.array(
        [pnt3(curve.Value(t0 + (t1 - t0) * i / (n - 1))) for i in range(n)]
    )


def fit_plane(points: np.ndarray):
    center = points.mean(axis=0)
    q = points - center
    _, _, vh = np.linalg.svd(q, full_matrices=False)
    normal = vh[-1]
    deviation = float(np.abs(q @ normal).max())
    return center, normal, deviation


def read_step(path: str | Path):
    reader = STEPControl_Reader()
    status = reader.ReadFile(str(path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"无法读取 STEP 文件（状态 {status}）")
    reader.TransferRoots()
    shape = reader.OneShape()
    if shape.IsNull():
        raise RuntimeError("STEP 文件不包含可用的几何体")
    return shape


def write_step(shape, path: str | Path) -> None:
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_StepModelType.STEPControl_AsIs)
    status = writer.Write(str(path))
    if status != IFSelect_RetDone:
        raise RuntimeError(f"STEP 写出失败（状态 {status}）")


def tessellate_stl(
    shape,
    path: str | Path,
    deflection: float = 0.12,
    angle: float = 0.35,
) -> None:
    BRepMesh_IncrementalMesh(shape, deflection, False, angle, True)
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    if not writer.Write(shape, str(path)):
        raise RuntimeError("三角网格导出失败")


_STL_DTYPE = np.dtype(
    [
        ("normal", "<f4", (3,)),
        ("vertices", "<f4", (3, 3)),
        ("attr", "<u2"),
    ]
)


def stl_stats(path: str | Path) -> dict:
    """Volume/area of a binary STL, computed independently of BRepGProp."""
    raw = Path(path).read_bytes()
    if len(raw) < 84:
        return {"volume": 0.0, "area": 0.0, "triangles": 0}
    count = struct.unpack("<I", raw[80:84])[0]
    if len(raw) < 84 + count * 50:
        # possibly ASCII STL - fall back to zero rather than reporting nonsense
        return {"volume": 0.0, "area": 0.0, "triangles": 0}
    tris = np.frombuffer(raw, dtype=_STL_DTYPE, count=count, offset=84)
    v = tris["vertices"].astype(np.float64)
    cr = np.cross(v[:, 1], v[:, 2])
    volume = abs(float(np.einsum("ij,ij->i", v[:, 0], cr).sum() / 6.0))
    area = float((np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1) / 2.0).sum())
    return {"volume": volume, "area": area, "triangles": count}


# ---------------------------------------------------------------------------
# sections
# ---------------------------------------------------------------------------


@dataclass
class BuildOptions:
    half_samples: int = 65
    # auto = detect the part's reflection-symmetry axis; otherwise x / y / z
    parting_axis: str = "auto"
    sew_tolerance: float = 1e-4
    mesh_deflection: float = 0.12
    mesh_angle: float = 0.35
    # none: original reconstruction; A: trim free end; B: adjust free end
    repair_mode: str = "none"

    def clean(self) -> "BuildOptions":
        h = int(self.half_samples)
        h = max(17, min(161, h))
        if h % 2 == 0:
            h += 1
        raw = str(self.parting_axis).strip().lower()
        # backwards compatibility with the first UI version
        raw = {"local_y": "auto", "global_y": "y"}.get(raw, raw)
        axis = raw if raw in {"auto", "x", "y", "z"} else "auto"
        repair_mode = str(self.repair_mode).strip().upper()
        if repair_mode not in {"A", "B"}:
            repair_mode = "NONE"
        return BuildOptions(
            half_samples=h,
            parting_axis=axis,
            sew_tolerance=max(1e-6, min(0.1, float(self.sew_tolerance))),
            mesh_deflection=max(0.02, min(0.5, float(self.mesh_deflection))),
            mesh_angle=max(0.15, min(0.8, float(self.mesh_angle))),
            repair_mode=repair_mode,
        )


@dataclass
class Section:
    index: int
    edge: object
    points: np.ndarray
    center: np.ndarray
    normal: np.ndarray
    side: np.ndarray
    up: np.ndarray
    length: float
    axis_name: str = ""
    symmetry_scores: dict = field(default_factory=dict)
    t_right: float = 0.0
    t_left: float = 0.0
    right: np.ndarray = field(default_factory=lambda: np.zeros(3))
    left: np.ndarray = field(default_factory=lambda: np.zeros(3))
    tangent_right: np.ndarray = field(default_factory=lambda: np.zeros(3))
    tangent_left: np.ndarray = field(default_factory=lambda: np.zeros(3))
    top_pts: np.ndarray | None = None
    bottom_pts: np.ndarray | None = None


def _dedupe_loops(candidates: list[dict]) -> list[dict]:
    """Merge geometrically identical loops created by adjacent strips/caps."""
    candidates.sort(key=lambda c: (c["center"][0], c["center"][1], c["center"][2]))
    clusters: list[list[dict]] = []
    for cand in candidates:
        for cluster in clusters:
            ref = cluster[0]
            if (
                np.linalg.norm(cand["center"] - ref["center"]) < 0.05
                and abs(cand["length"] - ref["length"]) < 0.05
            ):
                cluster.append(cand)
                break
        else:
            clusters.append([cand])
    # keep the loop with the most sample points-equivalent quality (longest)
    return [max(cluster, key=lambda c: c["length"]) for cluster in clusters]


def _mean_nearest_distance(points: np.ndarray, reflected: np.ndarray, chunk: int = 192) -> float:
    total = 0.0
    for start in range(0, len(reflected), chunk):
        block = reflected[start : start + chunk]
        diff = points[None, :, :] - block[:, None, :]
        dist = np.sqrt(np.einsum("ijk,ijk->ij", diff, diff))
        total += float(dist.min(axis=1).sum())
    return total / max(len(points), 1)


def detect_symmetry_axis(points: np.ndarray, spine_axis: np.ndarray | None = None):
    """Detect which global axis is the part's left/right reflection normal.

    A tube is symmetric about the plane between its two sides.  Reflecting the
    sampled surface across that plane gives near-zero nearest-neighbour error.
    For an X-aligned pipe that plane is usually Z=0 (sides at Z+/-), but it can
    just as well be Y=0 or X=0 for a differently oriented part.
    """
    points = np.asarray(points, dtype=float)
    if len(points) > 4500:
        points = points[np.linspace(0, len(points) - 1, 4500).astype(int)]
    lo = points.min(axis=0)
    hi = points.max(axis=0)
    center = (lo + hi) / 2.0

    scores = {}
    for i, name in enumerate(("X", "Y", "Z")):
        reflected = points.copy()
        reflected[:, i] = 2.0 * center[i] - reflected[:, i]
        scores[name] = _mean_nearest_distance(points, reflected)

    order = sorted(scores, key=scores.get)
    chosen = order[0]
    if spine_axis is not None:
        # The parting line runs along the spine, so a symmetry axis parallel to
        # the spine is not a left/right side pair.
        def parallel(name: str) -> bool:
            vec = np.zeros(3)
            vec["XYZ".index(name)] = 1.0
            return abs(float(np.dot(vec, spine_axis))) > 0.92

        non_spine = [name for name in order if not parallel(name)]
        if non_spine:
            # prefer a transverse symmetry axis unless it is clearly worse
            if parallel(chosen) or scores[non_spine[0]] <= scores[chosen] * 1.35:
                chosen = non_spine[0]
    second = order[1] if order[0] != chosen else order[1] if len(order) > 1 else chosen
    confidence = 0.0
    others = [scores[n] for n in scores if n != chosen]
    if others:
        confidence = max(0.0, (min(others) - scores[chosen]) / max(min(others), 1e-9))
    return chosen, {k: round(float(v), 4) for k, v in scores.items()}, round(confidence, 4)


def extract_sections(
    shape,
    *,
    parting_axis: str = "auto",
    progress: ProgressCb | None = None,
    verbose: bool = False,
    warnings: list[str] | None = None,
) -> list[Section]:
    diag = shape_length(shape)
    candidates: list[dict] = []
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        edge = TopoDS.Edge_s(exp.Current())
        exp.Next()
        if not edge.Closed():
            continue
        length = edge_length(edge)
        if length < 0.02 * diag:
            continue
        pts = sample_edge(edge, 180)
        center, normal, deviation = fit_plane(pts)
        if deviation > max(0.05, 1e-4 * diag):
            continue
        candidates.append(
            {
                "edge": edge,
                "length": length,
                "center": center,
                "normal": normal,
                "points": pts,
            }
        )
    if progress:
        progress(0.35, f"发现 {len(candidates)} 个闭合截面环")
    chosen = _dedupe_loops(candidates)
    if len(chosen) < MIN_SECTIONS:
        raise RuntimeError(
            "未识别到足够的截面环。该算法需要输入模型由闭合、平面的截面轮廓组成。"
        )

    centers = np.array([c["center"] for c in chosen])
    mean = centers.mean(axis=0)
    _, _, vh = np.linalg.svd(centers - mean, full_matrices=False)
    spine_axis = vh[0]
    # Prefer +X when the part is roughly X-aligned; otherwise keep sign stable.
    if abs(float(np.dot(spine_axis, np.array([1.0, 0.0, 0.0])))) > 0.2:
        if np.dot(spine_axis, np.array([1.0, 0.0, 0.0])) < 0:
            spine_axis = -spine_axis
    order = np.argsort((centers - mean) @ spine_axis)
    chosen = [chosen[i] for i in order]

    # Concatenate a decimated point cloud from all sections and detect the
    # left/right reflection axis.  This replaces the old hard-coded Y choice.
    cloud = np.vstack([c["points"][::4] for c in chosen])
    if len(cloud) > 4500:
        cloud = cloud[np.linspace(0, len(cloud) - 1, 4500).astype(int)]
    requested = str(parting_axis).strip().lower()
    requested = {"local_y": "auto", "global_y": "y"}.get(requested, requested)
    scores = {}
    confidence = 0.0
    axis_name = "Z"
    if requested in {"x", "y", "z"}:
        axis_name = requested.upper()
        _, scores, confidence = detect_symmetry_axis(cloud, spine_axis)
    else:
        axis_name, scores, confidence = detect_symmetry_axis(cloud, spine_axis)
        requested = "auto"

    axis_index = "XYZ".index(axis_name)
    axis_vec = np.zeros(3)
    axis_vec[axis_index] = 1.0
    if abs(float(np.dot(axis_vec, spine_axis))) > 0.92:
        if warnings is not None:
            warnings.append(
                f"所选 {axis_name} 轴与管件长轴平行，无法作为左右分型方向；已自动改用其他轴。"
            )
        _, scores, confidence = detect_symmetry_axis(cloud, spine_axis)
        axis_name = min(
            (n for n in ("X", "Y", "Z") if abs(float(np.dot(np.eye(3)["XYZ".index(n)], spine_axis))) <= 0.92),
            key=scores.get,
            default=axis_name,
        )
        axis_index = "XYZ".index(axis_name)
        axis_vec = np.zeros(3)
        axis_vec[axis_index] = 1.0
    if verbose:
        print(f"symmetry axis: {axis_name} scores={scores} confidence={confidence}")

    sections: list[Section] = []
    prev_side: np.ndarray | None = None
    prev_up: np.ndarray | None = None
    for i, cand in enumerate(chosen):
        normal = cand["normal"]
        if i + 1 < len(chosen):
            direction = chosen[i + 1]["center"] - cand["center"]
        elif i > 0:
            direction = cand["center"] - chosen[i - 1]["center"]
        else:
            direction = spine_axis
        if np.dot(normal, direction) < 0:
            normal = -normal
        if i == 0 and np.dot(normal, spine_axis) < 0:
            normal = -normal

        # Project the detected global symmetry axis into this section plane.
        side = axis_vec - float(np.dot(axis_vec, normal)) * normal
        if float(np.dot(side, side)) < 0.04:
            fallback = np.array([0.0, 1.0, 0.0])
            fallback = fallback - float(np.dot(fallback, normal)) * normal
            if float(np.dot(fallback, fallback)) < 0.04:
                fallback = np.array([0.0, 0.0, 1.0])
                fallback = fallback - float(np.dot(fallback, normal)) * normal
            side = fallback
            if warnings is not None and i == 0:
                warnings.append("分型轴在截面内退化，已回退到局部方向。")
        side /= np.linalg.norm(side)
        if prev_side is not None and np.dot(side, prev_side) < 0:
            side = -side

        up = np.cross(normal, side)
        up /= np.linalg.norm(up)
        if prev_up is not None and np.dot(up, prev_up) < 0:
            side = -side
            up = -up

        sections.append(
            Section(
                index=i,
                edge=cand["edge"],
                points=cand["points"],
                center=cand["center"],
                normal=normal,
                side=side,
                up=up,
                length=cand["length"],
                axis_name=axis_name,
                symmetry_scores=scores,
            )
        )
        prev_side, prev_up = side, up

    if verbose:
        print(f"unique sections: {len(sections)}")
    return sections


def _refine_extreme(adaptor, t_guess, t0, t1, coord_fn, maximize: bool):
    span = t1 - t0
    lo = max(t0, t_guess - span * 0.012)
    hi = min(t1, t_guess + span * 0.012)
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c = hi - gr * (hi - lo)
    d = lo + gr * (hi - lo)
    fc, fd = coord_fn(c), coord_fn(d)
    for _ in range(70):
        if (fc > fd) == maximize:
            hi, d, fd = d, c, fc
            c = hi - gr * (hi - lo)
            fc = coord_fn(c)
        else:
            lo, c, fc = c, d, fd
            d = lo + gr * (hi - lo)
            fd = coord_fn(d)
        if hi - lo < 1e-10:
            break
    return (lo + hi) / 2.0


def prepare_section(sec: Section, half_samples: int = 65) -> Section:
    adaptor = BRepAdaptor_Curve(sec.edge)
    t0, t1 = adaptor.FirstParameter(), adaptor.LastParameter()
    period = t1 - t0

    def point(t):
        tt = t0 + ((t - t0) % period)
        return adaptor.Value(tt)

    def ucoord(t):
        return float(np.dot(pnt3(point(t)) - sec.center, sec.side))

    ts = np.linspace(t0, t1, 2401)
    us = np.array([ucoord(t) for t in ts])
    i_min = int(np.argmin(us))
    i_max = int(np.argmax(us))
    t_right = _refine_extreme(adaptor, ts[i_max], t0, t1, ucoord, True)
    t_left = _refine_extreme(adaptor, ts[i_min], t0, t1, ucoord, False)
    p_right = point(t_right)
    p_left = point(t_left)
    sec.t_right, sec.t_left = t_right, t_left
    sec.right = pnt3(p_right)
    sec.left = pnt3(p_left)

    tan = adaptor.DN(t_right, 1)
    tan_vec = np.array([tan.X(), tan.Y(), tan.Z()])
    direction = 1.0 if np.dot(tan_vec, sec.up) >= 0 else -1.0
    tan_right = direction * tan_vec
    tan_right /= np.linalg.norm(tan_right)

    tan_left_v = adaptor.DN(t_left, 1)
    tan_left = direction * np.array([tan_left_v.X(), tan_left_v.Y(), tan_left_v.Z()])
    tan_left /= np.linalg.norm(tan_left)
    sec.tangent_right = tan_right
    sec.tangent_left = tan_left

    def arc_points(start_t, end_t, n_dense=1200, n_out=half_samples) -> np.ndarray:
        total = (end_t - start_t) % period
        if direction < 0:
            total = period - total
        if total < 1e-9:
            total = period
        ts_dense = np.linspace(0.0, total, n_dense)
        pts = np.array([pnt3(point(start_t + direction * s)) for s in ts_dense])
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        cum /= cum[-1]
        targets = np.linspace(0.0, 1.0, n_out)
        out = np.empty((n_out, 3), dtype=float)
        for k, target in enumerate(targets):
            j = int(np.searchsorted(cum, target))
            j = min(max(j, 1), len(cum) - 1)
            f = (target - cum[j - 1]) / max(cum[j] - cum[j - 1], 1e-15)
            out[k] = pts[j - 1] + f * (pts[j] - pts[j - 1])
        return out

    top = arc_points(t_right, t_left)
    bottom = arc_points(t_left, t_right)
    top[0], top[-1] = sec.right, sec.left
    bottom[0], bottom[-1] = sec.left, sec.right
    sec.top_pts, sec.bottom_pts = top, bottom
    return sec


def prepare_sections(
    sections: Sequence[Section],
    half_samples: int,
    progress: ProgressCb | None = None,
) -> None:
    total = len(sections)
    for i, sec in enumerate(sections):
        prepare_section(sec, half_samples)
        if progress and (i % max(1, total // 12) == 0 or i == total - 1):
            progress(0.4 + 0.2 * (i + 1) / total, f"提取截面特征 {i + 1}/{total}")


# ---------------------------------------------------------------------------
# B-spline surface construction
# ---------------------------------------------------------------------------


def _pts_array(points: np.ndarray) -> TColgp_HArray1OfPnt:
    arr = TColgp_HArray1OfPnt(1, len(points))
    for i, p in enumerate(points):
        arr.SetValue(i + 1, gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
    return arr


def _params_array(n: int, values: Iterable[float] | None = None) -> TColStd_HArray1OfReal:
    arr = TColStd_HArray1OfReal(1, n)
    if values is None:
        values = np.linspace(0.0, 1.0, n)
    for i, value in enumerate(values):
        arr.SetValue(i + 1, float(value))
    return arr


def _fit_curve(points: np.ndarray, params: Sequence[float], tangents=None):
    interp = GeomAPI_Interpolate(
        _pts_array(points),
        _params_array(len(points), params),
        False,
        CURVE_TOL,
    )
    if tangents is not None:
        first, last = tangents
        interp.Load(
            gp_Vec(float(first[0]), float(first[1]), float(first[2])),
            gp_Vec(float(last[0]), float(last[1]), float(last[2])),
            True,
        )
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError("截面 B 样条插值失败")
    return interp.Curve()


def _curve_data(curve):
    poles = np.array(
        [pnt3(curve.Pole(i)) for i in range(1, curve.NbPoles() + 1)]
    )
    knots = [float(curve.Knot(i)) for i in range(1, curve.NbKnots() + 1)]
    mults = [int(curve.Multiplicity(i)) for i in range(1, curve.NbKnots() + 1)]
    return poles, knots, mults


def _surface_from_curves(u_curves: Sequence, v_params: Sequence[float]):
    _, u_knots, u_mults = _curve_data(u_curves[0])
    nu = u_curves[0].NbPoles()

    v_curves = []
    for k in range(nu):
        poles = np.array([pnt3(curve.Pole(k + 1)) for curve in u_curves])
        v_curves.append(_fit_curve(poles, v_params))
    _, v_knots, v_mults = _curve_data(v_curves[0])
    nv = v_curves[0].NbPoles()

    poles = TColgp_Array2OfPnt(1, nu, 1, nv)
    for k, curve in enumerate(v_curves):
        for j in range(1, nv + 1):
            poles.SetValue(k + 1, j, curve.Pole(j))

    uk = TColStd_Array1OfReal(1, len(u_knots))
    for i, value in enumerate(u_knots):
        uk.SetValue(i + 1, value)
    vk = TColStd_Array1OfReal(1, len(v_knots))
    for i, value in enumerate(v_knots):
        vk.SetValue(i + 1, value)
    um = TColStd_Array1OfInteger(1, len(u_mults))
    for i, value in enumerate(u_mults):
        um.SetValue(i + 1, value)
    vm = TColStd_Array1OfInteger(1, len(v_mults))
    for i, value in enumerate(v_mults):
        vm.SetValue(i + 1, value)

    return Geom_BSplineSurface(poles, uk, vk, um, vm, 3, 3, False, False)


def _build_half_surface(sections: Sequence[Section], side: str, v_params: Sequence[float]):
    u_curves = []
    for sec in sections:
        if side == "top":
            pts = sec.top_pts
            tangents = (sec.tangent_right, sec.tangent_left)
        else:
            pts = sec.bottom_pts
            tangents = (sec.tangent_left, sec.tangent_right)
        if pts is None:
            raise RuntimeError("截面尚未准备")
        u_curves.append(_fit_curve(pts, np.linspace(0.0, 1.0, len(pts)), tangents))
    return _surface_from_curves(u_curves, v_params)


def _section_u_curves(sections: Sequence[Section], side: str):
    curves = []
    for sec in sections:
        if side == "top":
            pts, tangents = sec.top_pts, (sec.tangent_right, sec.tangent_left)
        else:
            pts, tangents = sec.bottom_pts, (sec.tangent_left, sec.tangent_right)
        if pts is None:
            raise RuntimeError("截面尚未准备")
        curves.append(_fit_curve(pts, np.linspace(0.0, 1.0, len(pts)), tangents))
    return curves


def _fair_last_band_surface(sections: Sequence[Section], side: str):
    """Path A: keep all section curves, fair only the 136->137 band.

    The first part is a normal cubic loft through sections 0..136.  The last
    band is a quintic Bezier joined with G1 tangent continuity at 136.  Its
    initial curvature is zero, which prevents the cubic interpolation from
    concentrating curvature at 136.  Both pieces are merged into one B-spline
    surface, so there is no extra topological edge at 136.
    """
    u_curves = _section_u_curves(sections, side)
    nu = u_curves[0].NbPoles()
    _, u_knots, u_mults = _curve_data(u_curves[0])
    centers = np.array([s.center for s in sections])
    fd = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(centers[:-1], axis=0), axis=1))]
    first_len = float(fd[-1])
    band_len = float(np.linalg.norm(centers[-1] - centers[-2]))
    first_params = fd / first_len

    combined = []
    for k in range(nu):
        poles = np.array([pnt3(c.Pole(k + 1)) for c in u_curves])
        first = _fit_curve(poles[:-1], first_params)
        p0 = first.Value(first.LastParameter())
        d1 = first.DN(first.LastParameter(), 1)
        p0 = np.array([p0.X(), p0.Y(), p0.Z()])
        t0 = np.array([d1.X(), d1.Y(), d1.Z()]) * (band_len / first_len)
        p1 = poles[-1]
        t1 = p1 - p0
        b0 = p0
        b1 = p0 + t0 / 5.0
        b2 = 2.0 * b1 - b0
        b5 = p1
        b4 = p1 - t1 / 5.0
        b3 = 2.0 * b4 - b5
        arr = TColgp_Array1OfPnt(1, 6)
        for i, q in enumerate((b0, b1, b2, b3, b4, b5)):
            arr.SetValue(i + 1, gp_Pnt(float(q[0]), float(q[1]), float(q[2])))
        bezier = Geom_BezierCurve(arr)
        merger = GeomConvert_CompCurveToBSplineCurve(first)
        if not merger.Add(bezier, 1e-6, True, True, 2):
            raise RuntimeError("大径端光顺曲面拼接失败")
        combined.append(merger.BSplineCurve())

    if len({c.NbPoles() for c in combined}) != 1 or len({c.NbKnots() for c in combined}) != 1:
        raise RuntimeError("大径端光顺曲面的参数不一致")
    v_degree = combined[0].Degree()
    nv = combined[0].NbPoles()
    _, v_knots, v_mults = _curve_data(combined[0])
    poles = TColgp_Array2OfPnt(1, nu, 1, nv)
    for k, curve in enumerate(combined):
        for j in range(1, nv + 1):
            poles.SetValue(k + 1, j, curve.Pole(j))

    uk = TColStd_Array1OfReal(1, len(u_knots))
    for i, value in enumerate(u_knots):
        uk.SetValue(i + 1, value)
    vk = TColStd_Array1OfReal(1, len(v_knots))
    for i, value in enumerate(v_knots):
        vk.SetValue(i + 1, value)
    um = TColStd_Array1OfInteger(1, len(u_mults))
    for i, value in enumerate(u_mults):
        um.SetValue(i + 1, value)
    vm = TColStd_Array1OfInteger(1, len(v_mults))
    for i, value in enumerate(v_mults):
        vm.SetValue(i + 1, value)
    return Geom_BSplineSurface(poles, uk, vk, um, vm, 3, v_degree, False, False)


def _shifted_copy_136(sections: Sequence[Section], millimeters: float):
    if millimeters <= 0:
        return list(sections)
    sec = sections[-2]
    out = list(sections)
    mod = copy.copy(sec)
    spine = sections[0].normal / max(float(np.linalg.norm(sections[0].normal)), 1e-12)
    delta = spine * float(millimeters)
    mod.center = sec.center + delta
    mod.top_pts = sec.top_pts + delta
    mod.bottom_pts = sec.bottom_pts + delta
    mod.right = sec.right + delta
    mod.left = sec.left + delta
    out[-2] = mod
    return out


def _end_normal_peak(sections: Sequence[Section]) -> float:
    centers = np.array([s.center for s in sections])
    dist = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))]
    vp = dist / dist[-1]
    top = _build_half_surface(sections, "top", vp)
    vs = np.linspace(0.82, 1.0, 181)
    normals = []
    points = []
    for v in vs:
        props = GeomLProp_SLProps(top, 0.5, float(v), 1, 1e-7)
        n = props.Normal()
        normals.append([n.X(), n.Y(), n.Z()])
        p = top.Value(0.5, float(v))
        points.append([p.X(), p.Y(), p.Z()])
    normals = np.array(normals)
    points = np.array(points)
    seg = np.linalg.norm(np.diff(points, axis=0), axis=1)
    angles = np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", normals[:-1], normals[1:]), -1, 1)))
    return float((angles / np.maximum(seg, 1e-9)).max())


def _shifted_copy_end(sections: Sequence[Section], shift_136: float, shift_137: float):
    out = list(sections)
    spine = sections[0].normal / max(float(np.linalg.norm(sections[0].normal)), 1e-12)
    if shift_136:
        sec = sections[-2]
        mod = copy.copy(sec)
        delta = spine * float(shift_136)
        mod.center = sec.center + delta
        mod.top_pts = sec.top_pts + delta
        mod.bottom_pts = sec.bottom_pts + delta
        mod.right = sec.right + delta
        mod.left = sec.left + delta
        out[-2] = mod
    if shift_137:
        sec = sections[-1]
        mod = copy.copy(sec)
        delta = spine * float(shift_137)
        mod.center = sec.center + delta
        mod.top_pts = sec.top_pts + delta
        mod.bottom_pts = sec.bottom_pts + delta
        mod.right = sec.right + delta
        mod.left = sec.left + delta
        out[-1] = mod
    return out


def _optimize_end_shifts(
    sections: Sequence[Section], maximum_136: float, maximum_137: float
) -> tuple[float, float, float]:
    """Search 136 and 137 axial shifts; 137 width/height/flatness stay fixed."""
    if maximum_136 <= 0 and maximum_137 <= 0:
        return 0.0, 0.0, _end_normal_peak(sections)

    cand136 = {0.0}
    if maximum_136 > 0:
        for f in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
            if f <= maximum_136:
                cand136.add(f)
        cand136.add(float(maximum_136))
    cand137 = {0.0}
    if maximum_137 > 0:
        for f in (-1.0, -0.5, 0.5, 1.0):
            cand137.add(float(maximum_137) * f)
        cand137.add(float(maximum_137))
    best = (float("inf"), 0.0, 0.0)
    for d136 in sorted(cand136):
        for d137 in sorted(cand137):
            if d136 == 0.0 and d137 == 0.0:
                peak = _end_normal_peak(sections)
            else:
                try:
                    peak = _end_normal_peak(_shifted_copy_end(sections, d136, d137))
                except Exception:
                    continue
            if peak < best[0]:
                best = (peak, d136, d137)
    return best[1], best[2], best[0]


def _shift_section_136(sections: Sequence[Section], millimeters: float) -> float:
    """Path B: shift only section 136 along the spine; 137 stays fixed."""
    if len(sections) < 3 or millimeters <= 0:
        return 0.0
    spine = sections[0].normal.copy()
    spine /= max(float(np.linalg.norm(spine)), 1e-12)
    delta = spine * float(millimeters)
    sec = sections[-2]
    sec.center = sec.center + delta
    sec.top_pts = sec.top_pts + delta
    sec.bottom_pts = sec.bottom_pts + delta
    sec.right = sec.right + delta
    sec.left = sec.left + delta
    return float(millimeters)


def _shift_section_137(sections: Sequence[Section], millimeters: float) -> float:
    if len(sections) < 3 or abs(millimeters) <= 1e-12:
        return 0.0
    spine = sections[0].normal.copy()
    spine /= max(float(np.linalg.norm(spine)), 1e-12)
    delta = spine * float(millimeters)
    sec = sections[-1]
    sec.center = sec.center + delta
    sec.top_pts = sec.top_pts + delta
    sec.bottom_pts = sec.bottom_pts + delta
    sec.right = sec.right + delta
    sec.left = sec.left + delta
    return float(millimeters)


def _make_cap(top_surface, bottom_surface, v: float):
    edges = []
    for surface in (top_surface, bottom_surface):
        curve = surface.VIso(v)
        edges.append(BRepBuilderAPI_MakeEdge(curve).Edge())
    wire_maker = BRepBuilderAPI_MakeWire(edges[0], edges[1])
    if not wire_maker.IsDone():
        # try the other orientation
        wire_maker = BRepBuilderAPI_MakeWire(edges[1], edges[0])
    if not wire_maker.IsDone():
        raise RuntimeError("端面轮廓线无法闭合")
    face_maker = BRepBuilderAPI_MakeFace(wire_maker.Wire(), True)
    if not face_maker.IsDone():
        raise RuntimeError("端盖平面构造失败")
    return face_maker.Face()


def _sew_faces(faces: Sequence, tolerance: float):
    sew = BRepBuilderAPI_Sewing(tolerance)
    for face in faces:
        sew.Add(face)
    sew.Perform()
    return sew.SewedShape()


def _largest_shell(shape):
    shells = []
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        shells.append(TopoDS.Shell_s(exp.Current()))
        exp.Next()
    if not shells:
        raise RuntimeError("缝合后没有生成壳体")
    return max(shells, key=lambda s: float(np.prod(np.maximum(bbox_size(shape_bbox(s)), 1e-9))))


def _to_solid(sewed):
    shell = _largest_shell(sewed)
    solid = BRepBuilderAPI_MakeSolid(shell).Solid()
    fixer = ShapeFix_Solid(solid)
    fixer.Perform()
    return fixer.Solid()


def _seam_max_angle(top_surface, bottom_surface, samples: int = 21) -> float:
    """Maximum normal angle across the two parting seams (0 = perfect G1)."""
    worst = 0.0
    for v in np.linspace(0.0, 1.0, samples):
        for u_top, u_bottom in ((0.0, 1.0), (1.0, 0.0)):
            props_top = GeomLProp_SLProps(top_surface, u_top, float(v), 1, 1e-7)
            props_bottom = GeomLProp_SLProps(bottom_surface, u_bottom, float(v), 1, 1e-7)
            n1, n2 = props_top.Normal(), props_bottom.Normal()
            dot = abs(n1.X() * n2.X() + n1.Y() * n2.Y() + n1.Z() * n2.Z())
            dot = max(-1.0, min(1.0, dot))
            worst = max(worst, math.degrees(math.acos(dot)))
    return worst


def _parting_edges(solid, model_extent: float):
    mapping = face_edge_map(solid)
    result = []
    for i in range(1, mapping.Extent() + 1):
        edge = TopoDS.Edge_s(mapping.FindKey(i))
        if mapping.FindFromIndex(i).Extent() != 2:
            continue
        length = edge_length(edge)
        if length < 0.2 * model_extent:
            continue
        bb = shape_bbox(edge)
        edge_span = float(max(bbox_size(bb)))
        if edge_span > 0.5 * model_extent:
            result.append(edge)
    # MapShapesAndAncestors keeps one occurrence per orientation, so the same
    # seam edge can appear twice.  IsSame ignores orientation and collapses it.
    unique = []
    for edge in result:
        if any(edge.IsSame(seen) for seen in unique):
            continue
        unique.append(edge)
    unique.sort(key=edge_length, reverse=True)
    return unique[:2]


@dataclass
class BuildResult:
    solid: object
    sections: list[Section]
    top_face: object
    bottom_face: object
    cap_faces: list
    parting_edges: list
    build_seconds: float
    u_poles: int = 0
    v_poles: int = 0
    sew_tolerance: float = 0.0
    seam_max_angle_deg: float = 0.0
    parting_axis: str = ""
    symmetry_scores: dict = field(default_factory=dict)
    repair_mode: str = "none"
    repaired_indices: list[int] = field(default_factory=list)
    removed_indices: list[int] = field(default_factory=list)
    terminal_anomaly: bool = False
    shift_136_mm: float = 0.0
    shift_137_mm: float = 0.0
    repair_messages: list[str] = field(default_factory=list)
    parting_trend_max_dev_mm: float = 0.0
    parting_trend_warning: str | None = None
    warnings: list[str] = field(default_factory=list)


def build_solid(
    shape,
    options: BuildOptions,
    progress: ProgressCb | None = None,
) -> BuildResult:
    options = options.clean()
    started = time.time()
    warnings: list[str] = []

    def report(value: float, text: str):
        if progress:
            progress(value, text)

    report(0.04, "读取模型拓扑")
    sections = extract_sections(
        shape,
        parting_axis=options.parting_axis,
        progress=lambda p, t: report(0.05 + 0.25 * p, t),
        warnings=warnings,
    )
    if len(sections) < MIN_SECTIONS:
        raise RuntimeError("截面数量不足，无法重建")

    report(0.32, f"识别到 {len(sections)} 个截面，提取分型点")
    prepare_sections(sections, options.half_samples, progress=progress)

    from .repair import RepairReport, detect_and_repair

    snapshot = [
        (
            s.center.copy(),
            s.top_pts.copy(),
            s.bottom_pts.copy(),
            s.tangent_right.copy(),
            s.tangent_left.copy(),
            s.right.copy(),
            s.left.copy(),
        )
        for s in sections
    ]
    if options.repair_mode == "NONE":
        repair_report = RepairReport(mode="NONE")
    else:
        repair_report = detect_and_repair(sections, options.repair_mode, warnings)
    shift_136 = 0.0
    shift_137 = 0.0
    if repair_report.terminal_anomaly and repair_report.repaired_indices:
        if options.repair_mode == "A":
            # A: the terminal section is a free extension.  Try dropping one
            # to three terminal sections and keep the smoothest remaining end.
            cut0 = min(repair_report.repaired_indices)
            min_cut = max(0, len(sections) - 3)
            best_cut = cut0
            best_peak = float("inf")
            for cut in range(cut0, min_cut - 1, -1):
                peak = _end_normal_peak(sections[:cut])
                if peak < best_peak:
                    best_peak = peak
                    best_cut = cut
            removed = [s.index for s in sections[best_cut:]]
            sections = sections[:best_cut]
            repair_report.repaired_indices = []
            repair_report.removed_indices = removed
            repair_report.messages = [
                "137 截面异常，已自动去掉最后 "
                + str(len(removed))
                + " 个自由延伸截面；输出端口回退到截面 "
                + str(sections[-1].index)
                + "。"
            ]
        else:
            try:
                best_136, best_137, best_peak = _optimize_end_shifts(sections, 2.4, 4.0)
                shift_136 = _shift_section_136(sections, best_136)
                shift_137 = _shift_section_137(sections, best_137)
                repair_report.messages.append(
                    f"B 方案同时微调末端中心：136 {shift_136:+.2f} mm、137 {shift_137:+.2f} mm，"
                    f"试算法线峰值 {best_peak:.2f}°/mm。"
                )
            except Exception as exc:  # noqa: BLE001
                repair_report.messages.append(f"末端中心微调跳过：{exc}")
    if repair_report.repaired_indices:
        post_peak = _end_normal_peak(sections)
        if post_peak > max(0.5, repair_report.max_normal_turn * 1.05):
            for s, snap in zip(sections, snapshot):
                s.center = snap[0]
                s.top_pts = snap[1]
                s.bottom_pts = snap[2]
                s.tangent_right = snap[3]
                s.tangent_left = snap[4]
                s.right = snap[5]
                s.left = snap[6]
            repair_report.repaired_indices = []
            repair_report.messages = [
                "137 截面异常已检测到，但自动补齐后法线峰值反而升高，已回退原截面；请检查截面数据。"
            ]
    warnings.extend(repair_report.messages)

    centers = np.array([s.center for s in sections])
    distances = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))]
    if distances[-1] < 1e-9:
        distances = np.linspace(0.0, 1.0, len(sections))
    v_params = distances / distances[-1]

    report(0.62, "拟合两侧光滑 B 样条曲面")
    top_surface = _build_half_surface(sections, "top", v_params)
    bottom_surface = _build_half_surface(sections, "bottom", v_params)
    top_face = BRepBuilderAPI_MakeFace(top_surface, 1e-6).Face()
    bottom_face = BRepBuilderAPI_MakeFace(bottom_surface, 1e-6).Face()
    if top_face.IsNull() or bottom_face.IsNull():
        raise RuntimeError("侧面曲面构造失败")

    report(0.80, "构造平面端盖并缝合为实体")
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

    report(0.90, "检查实体有效性")
    if not BRepCheck_Analyzer(solid).IsValid():
        raise RuntimeError("生成的实体未通过 OCC 有效性检查")
    if count_subshapes(solid, TopAbs_SOLID) < 1:
        raise RuntimeError("没有生成实体")
    if free_edge_count(solid) > 0:
        raise RuntimeError("缝合后仍存在自由边，实体未闭合")

    bbox = shape_bbox(solid)
    extent = float(max(bbox_size(bbox)))
    parting = _parting_edges(solid, extent)
    seam_angle = _seam_max_angle(top_surface, bottom_surface)
    if len(parting) != 2:
        warnings.append(f"检测到 {len(parting)} 条纵向分型边，期望 2 条")
    elif abs(edge_length(parting[0]) - edge_length(parting[1])) > 0.2 * extent:
        warnings.append("左右分型边长度差异较大，请检查截面坐标方向")

    report(0.95, "生成预览与输出文件")
    return BuildResult(
        solid=solid,
        sections=sections,
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
        repair_mode=options.repair_mode,
        repaired_indices=repair_report.repaired_indices,
        removed_indices=repair_report.removed_indices,
        terminal_anomaly=repair_report.terminal_anomaly,
        shift_136_mm=shift_136,
        shift_137_mm=shift_137,
        repair_messages=repair_report.messages,
        parting_trend_max_dev_mm=repair_report.parting_trend_max_dev_mm,
        parting_trend_warning=repair_report.parting_trend_warning,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# statistics and previews
# ---------------------------------------------------------------------------


def input_stats(shape, sections: Sequence[Section], mesh: dict) -> dict:
    bb = shape_bbox(shape)
    return {
        "bbox": [round(v, 4) for v in bb],
        "size": [round(v, 4) for v in bbox_size(bb)],
        "faces": count_subshapes(shape, TopAbs_FACE),
        "edges": count_subshapes(shape, TopAbs_EDGE),
        "solids": count_subshapes(shape, TopAbs_SOLID),
        "shells": count_subshapes(shape, TopAbs_SHELL),
        "sections": len(sections),
        "parting_axis": sections[0].axis_name if sections else "",
        "symmetry_scores": sections[0].symmetry_scores if sections else {},
        "valid": bool(BRepCheck_Analyzer(shape).IsValid()),
        "free_edges": free_edge_count(shape),
        "volume": round(float(mesh["volume"]), 1),
        "area": round(float(mesh["area"]), 1),
        "triangles": int(mesh["triangles"]),
    }


def output_stats(result: BuildResult, mesh: dict) -> dict:
    solid = result.solid
    bb = shape_bbox(solid)
    mapping = face_edge_map(solid)
    shared = sum(1 for i in range(1, mapping.Extent() + 1) if mapping.FindFromIndex(i).Extent() == 2)
    parting_lengths = [round(edge_length(e), 2) for e in result.parting_edges]
    return {
        "bbox": [round(v, 4) for v in bb],
        "size": [round(v, 4) for v in bbox_size(bb)],
        "faces": count_subshapes(solid, TopAbs_FACE),
        "edges": count_subshapes(solid, TopAbs_EDGE),
        "solids": count_subshapes(solid, TopAbs_SOLID),
        "shells": count_subshapes(solid, TopAbs_SHELL),
        "sections": len(result.sections),
        "parting_axis": result.parting_axis,
        "symmetry_scores": result.symmetry_scores,
        "valid": bool(BRepCheck_Analyzer(solid).IsValid()),
        "watertight": free_edge_count(solid) == 0,
        "free_edges": free_edge_count(solid),
        "shared_edges": shared,
        "parting_edges": len(result.parting_edges),
        "parting_lengths": parting_lengths,
        "volume": round(float(mesh["volume"]), 1),
        "area": round(float(mesh["area"]), 1),
        "triangles": int(mesh["triangles"]),
        "build_seconds": round(result.build_seconds, 2),
        "u_poles": result.u_poles,
        "v_poles": result.v_poles,
        "sew_tolerance": result.sew_tolerance,
        "seam_max_angle_deg": round(result.seam_max_angle_deg, 6),
        "repair_mode": result.repair_mode,
        "repaired_indices": result.repaired_indices,
        "removed_indices": result.removed_indices,
        "terminal_anomaly": result.terminal_anomaly,
        "shift_136_mm": round(result.shift_136_mm, 3),
        "shift_137_mm": round(result.shift_137_mm, 3),
        "repair_messages": result.repair_messages,
        "parting_trend_max_dev_mm": round(result.parting_trend_max_dev_mm, 4),
        "parting_trend_warning": result.parting_trend_warning,
        "warnings": result.warnings,
    }


def _rounded(points: np.ndarray, decimals: int = 3):
    return [[round(float(v), decimals) for v in p] for p in points]


def input_section_polylines(sections: Sequence[Section], n: int = 72) -> list[dict]:
    polylines = []
    for sec in sections:
        curve = BRepAdaptor_Curve(sec.edge)
        t0, t1 = curve.FirstParameter(), curve.LastParameter()
        pts = [pnt3(curve.Value(t0 + (t1 - t0) * i / (n - 1))) for i in range(n)]
        pts[0] = pts[-1]  # closed
        polylines.append(
            {"name": f"section-{sec.index}", "type": "section", "points": _rounded(np.array(pts))}
        )
    return polylines


def parting_polylines(edges: Sequence, n: int = 320) -> list[dict]:
    polylines = []
    for i, edge in enumerate(edges):
        curve = BRepAdaptor_Curve(edge)
        t0, t1 = curve.FirstParameter(), curve.LastParameter()
        pts = [pnt3(curve.Value(t0 + (t1 - t0) * k / (n - 1))) for k in range(n)]
        polylines.append(
            {"name": f"parting-{i + 1}", "type": "parting", "points": _rounded(np.array(pts))}
        )
    return polylines


def write_preview(shape, path: str | Path, options: BuildOptions) -> dict:
    tessellate_stl(shape, path, options.mesh_deflection, options.mesh_angle)
    return stl_stats(path)


def save_json(path: str | Path, data) -> None:
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
