"""Native STEP plane sections. CAD curves drive measurements, never STL hulls.

The center is the area centroid enclosed by the *outer* wire. Wall thickness
is sampled shortest distance to the inner contour in the section plane, with
uniform outer arc-length weighting. It is not a radial/support-function gap
and is not a 3-D surface-normal thickness on steep longitudinal tapers.
"""
from __future__ import annotations

import os

import numpy as np
from shapely.geometry import Polygon, LineString

from .geometry import orthonormal_basis


# OCCT 内部并行布尔（BOP）开关。默认打开：布尔求交是单站耗时的绝对大头，
# 实测 24TK_1324-1 同一批 20 站 0.262 → 0.141 s/站（布尔 228 → 107 ms，2.1×），
# 面积/轮廓点数/壁厚逐位一致。若遇到并发或稳定性问题，用
# GTAPP_OCCT_PARALLEL=0 回退到串行（进程内有效，需重启服务）。
_RUN_PARALLEL = os.environ.get("GTAPP_OCCT_PARALLEL", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)



def _xyz(p):
    return np.array(p.Coord(), dtype=float)


def read_shape(path):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.TransferRoots():
        raise ValueError("STP 文件读取失败")
    shape = reader.OneShape()
    if shape.IsNull():
        raise ValueError("STP 文件中没有几何体")
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    while exp.More():
        count += 1
        exp.Next()
    if count != 1:
        raise ValueError(f"需要单个封闭管件实体，当前包含 {count} 个实体；请分离装配体或修复开放曲面")
    return shape


def shape_points(shape):
    """Sample CAD edges for the initial axis only; no tessellation dependency."""
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    points = []
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        c = BRepAdaptor_Curve(TopoDS.Edge_s(exp.Current()))
        for t in np.linspace(c.FirstParameter(), c.LastParameter(), 17):
            points.append(_xyz(c.Value(float(t))))
        exp.Next()
    return np.unique(np.round(points, 7), axis=0)


def _wire_points(wire, origin, u, v, deflection):
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection
    from OCP.TopAbs import TopAbs_REVERSED
    parts = []
    exp = BRepTools_WireExplorer(wire)
    while exp.More():
        edge = exp.Current()
        curve = BRepAdaptor_Curve(edge)
        samples = GCPnts_QuasiUniformDeflection(curve, float(deflection))
        if not samples.IsDone():
            raise ValueError("截面曲线离散失败")
        pts = np.array([_xyz(samples.Value(i)) for i in range(1, samples.NbPoints() + 1)])
        if edge.Orientation() == TopAbs_REVERSED:
            pts = pts[::-1]
        parts.append(pts[:-1])
        exp.Next()
    if not parts:
        raise ValueError("截面轮廓为空")
    xyz = np.vstack(parts)
    rel = xyz - origin
    xy = np.column_stack((rel @ u, rel @ v))
    # Some STEP B-spline straight edges yield thousands of redundant samples.
    # This bounded simplification affects display/distance sampling only.
    line = LineString(np.vstack((xy, xy[0]))).simplify(deflection * 0.1, preserve_topology=False)
    return np.asarray(line.coords)[:-1]


def contour_thickness(outer, inner, samples=192):
    """Uniform arc-length samples, distance to segments (not vertices)."""
    ends = np.roll(outer, -1, axis=0)
    lens = np.linalg.norm(ends - outer, axis=1)
    keep = lens > 1e-10
    outer, ends, lens = outer[keep], ends[keep], lens[keep]
    cum = np.r_[0., np.cumsum(lens)]
    targets = (np.arange(samples) + 0.5) * cum[-1] / samples
    idx = np.minimum(np.searchsorted(cum, targets, side="right") - 1, len(lens) - 1)
    pts = outer[idx] + ((targets - cum[idx]) / lens[idx])[:, None] * (ends[idx] - outer[idx])
    seg = np.roll(inner, -1, axis=0) - inner
    d2 = np.sum(seg * seg, axis=1)
    rel = pts[:, None, :] - inner[None, :, :]
    t = np.clip(np.sum(rel * seg[None, :, :], axis=2) / np.maximum(d2, 1e-20), 0., 1.)
    distances = np.sqrt(np.min(np.sum((rel - t[:, :, None] * seg[None, :, :]) ** 2, axis=2), axis=1))
    return {"median_mm": float(np.median(distances)), "mean_mm": float(np.mean(distances)),
            "min_mm": float(np.min(distances)), "max_mm": float(np.max(distances)),
            "sample_count": samples, "values_mm": distances.tolist(), "points_2d": pts.tolist()}


def section_shape(shape, origin, normal, *, details=True, deflection=0.003):
    """Measure a section, with a bounded re-cut at ambiguous CAD seams.

    A shifted plane is an actual new measurement, and its centroid/outline
    carry that new location. It is never assigned back to the original plane.
    """
    result = _section_shape_once(shape, origin, normal, details=details, deflection=deflection)
    if result['valid']:
        result['plane_offset_mm'] = 0.
        return result
    if result['reason'] in ('open_contour', 'non_nested_walls', 'not_two_nested_walls'):
        # Imported seam micro-edges can occur twice in the intersection graph.
        # A 0.1 micron boolean tolerance resolves them without joining real gaps.
        retry = _section_shape_once(shape, origin, normal, details=details,
                                    deflection=.0005, fuzzy=.0001)
        if retry['valid']:
            retry['plane_offset_mm'] = 0.
            return retry
    if result['reason'] == 'non_nested_walls' or (result['reason'] == 'not_two_nested_walls' and result.get('ring_count', 0) > 2):
        n = np.asarray(normal, dtype=float)
        n /= np.linalg.norm(n)
        for offset in (.0001, -.0001, .001, -.001, .005, -.005):
            retry = _section_shape_once(shape, np.asarray(origin) + offset * n, n,
                                        details=details, deflection=.0005)
            if retry['valid']:
                retry['plane_offset_mm'] = offset
                return retry
    return result


def _section_shape_once(shape, origin, normal, *, details=True, deflection=0.003, fuzzy=0.):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_HSequenceOfShape
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
    from OCP.gp import gp_Pln, gp_Pnt, gp_Dir

    origin = np.asarray(origin, dtype=float)
    n, u, v = orthonormal_basis(normal)
    plane = gp_Pln(gp_Pnt(*origin), gp_Dir(*n))
    op = BRepAlgoAPI_Section(shape, plane, False)
    # 并行只影响内部计算顺序，不改变几何定义；实测读数逐位一致。
    op.SetRunParallel(_RUN_PARALLEL)
    if fuzzy:
        op.SetFuzzyValue(fuzzy)
    # Leave Approximation at its default False. Re-approximating intersection
    # B-splines can split otherwise shared endpoints on imported STEP faces.
    op.Build()
    if not op.IsDone():
        return {"valid": False, "reason": "section_failed"}
    edges = TopTools_HSequenceOfShape()
    exp = TopExp_Explorer(op.Shape(), TopAbs_EDGE)
    while exp.More():
        edges.Append(exp.Current())
        exp.Next()
    if not edges.Length():
        return {"valid": False, "reason": "empty_section"}
    wires = TopTools_HSequenceOfShape()
    ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(edges, 1e-5, False, wires)
    rings = []
    for i in range(1, wires.Length() + 1):
        wire = TopoDS.Wire_s(wires.Value(i))
        if not wire.Closed():
            return {"valid": False, "reason": "open_contour"}
        face = BRepBuilderAPI_MakeFace(plane, wire, True)
        if not face.IsDone():
            return {"valid": False, "reason": "invalid_contour"}
        xy = _wire_points(wire, origin, u, v, deflection)
        polygon = Polygon(xy)
        area = abs(polygon.area)
        cx, cy = polygon.centroid.coords[0]
        center = origin + cx * u + cy * v
        if details:
            prop = GProp_GProps()
            BRepGProp.LinearProperties_s(wire, prop, False, False)
            perimeter = prop.Mass()
        else:
            perimeter = polygon.length  # Locator passes do not report measurements.
        rings.append({"wire": wire, "area": area, "center": center, "perimeter": perimeter, "xy": xy})
    # A one-loop sliver through an oblique end cap is not a tube section.
    if len(rings) != 2:
        return {"valid": False, "reason": "not_two_nested_walls", "ring_count": len(rings)}
    rings.sort(key=lambda r: r["area"], reverse=True)
    outer, inner = rings
    # Verify topology even in the centroid pass, so disconnected tubes cannot
    # accidentally be interpreted as an outer/inner pair.
    o = outer["xy"]
    inn = inner["xy"]
    po, pi = Polygon(o), Polygon(inn)
    repairs = []
    for name, poly in (("outer", po), ("inner", pi)):
        if not poly.is_valid:
            fixed = poly.buffer(0)
            delta = poly.boundary.hausdorff_distance(fixed.boundary)
            # Imported B-splines can overlap by a few microns at a shared
            # vertex. Repair only that bounded overlap, never bridge openings
            # or replace a non-convex ring by its convex hull.
            if (fixed.geom_type == "Polygon" and not fixed.interiors and delta <= .01
                    and abs(poly.area - fixed.area) <= .01 * poly.length):
                repairs.append({"wall": name, "max_displacement_mm": float(delta)})
                if name == "outer":
                    po, o = fixed, np.asarray(fixed.exterior.coords)[:-1]
                else:
                    pi, inn = fixed, np.asarray(fixed.exterior.coords)[:-1]
    if not po.is_valid or not pi.is_valid or not po.contains(pi):
        if deflection > .0005:
            # Near-tangent CAD seams can cross in a coarse chord approximation.
            # Re-sample the same CAD intersection before declaring it unusable.
            return _section_shape_once(shape, origin, n, details=details, deflection=.0005, fuzzy=fuzzy)
        from shapely.validation import explain_validity
        return {"valid": False, "reason": "non_nested_walls", "outer_validity": explain_validity(po),
                "inner_validity": explain_validity(pi), "contains": po.contains(pi)}
    cx, cy = po.centroid.coords[0]
    outer["center"] = origin + cx * u + cy * v
    outer["area"], inner["area"] = po.area, pi.area
    result = {"valid": True, "center": outer["center"], "normal": n,
              "perimeter": outer["perimeter"], "inner_perimeter": inner["perimeter"],
              "outer_area": outer["area"], "inner_area": inner["area"], "contour_repairs": repairs,
              "intersection_fuzzy_mm": fuzzy}
    if details:
        shift = np.array([(outer["center"] - origin) @ u, (outer["center"] - origin) @ v])
        o, inn = o - shift, inn - shift
        result.update({"outer_2d": o, "inner_2d": inn, "basis_u": u, "basis_v": v,
                       "outline_3d": outer["center"] + o[:, :1] * u + o[:, 1:] * v,
                       "inner_outline_3d": outer["center"] + inn[:, :1] * u + inn[:, 1:] * v,
                       "thickness": contour_thickness(o, inn)})
    return result
