"""Geometric self-check for a generated RFM solid.

``valid_solid`` only proves the shape is a single closed solid. A ruled loft
that pairs two sections half a turn apart still satisfies that: the shell is
closed, topology is valid, and only the volume integral falls apart because the
folded bands cancel. That is how a broken outer cavity reached the customer as
a "valid" model.

These checks look at the surface instead of the topology. Each shared edge
between two generated bands is compared by the angle between the two surface
normals there; a fold shows up as a near-90 deg crease, while a healthy tapered
loft stays within a few degrees.

Cost is one surface projection per shared edge per side, well under a second
for a 120 band shaping solid. Very large models skip the seam sweep and say so.
"""
from __future__ import annotations

import math

import numpy as np

from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.GeomLProp import GeomLProp_SLProps
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape, TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS

from core.common.solid_geometry import volume

# A lateral crease this sharp is never real tube geometry.
SEAM_REJECT_DEG = 45.0
# Worth telling the engineer about even though the part is still usable.
SEAM_WARN_DEG = 30.0
# Faces below this area are slivers; they break downstream booleans.
SLIVER_WARN_MM2 = 2.0
# Give up on the seam sweep rather than stall generation on a huge model.
MAX_EDGES_FOR_SEAM_SWEEP = 6000


def _surface_type(face):
    try:
        return int(BRepAdaptor_Surface(face, True).GetType())
    except Exception:
        return -1


def _area(face):
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return float(props.Mass())


def _normal_at(face, point):
    adaptor = BRepAdaptor_Surface(face, True)
    surface = adaptor.Surface().Surface()
    projection = GeomAPI_ProjectPointOnSurf(point, surface)
    if projection.NbPoints() < 1:
        return None
    u, v = projection.LowerDistanceParameters()
    props = GeomLProp_SLProps(surface, u, v, 1, 1e-9)
    if not props.IsNormalDefined():
        return None
    direction = props.Normal()
    normal = np.array([direction.X(), direction.Y(), direction.Z()], float)
    length = float(np.linalg.norm(normal))
    if length < 1e-12:
        return None
    normal /= length
    return -normal if face.Orientation() == TopAbs_REVERSED else normal


def _midpoint(edge):
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    if not (math.isfinite(first) and math.isfinite(last)) or last <= first:
        return None
    return curve.Value(0.5 * (first + last))


def _count_solids(shape):
    count = 0
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def inspect_solid(shape) -> dict:
    """Measure surface continuity and report it; never raises on a bad part.

    Returns ``status`` in ``{'ok', 'warning', 'reject'}`` together with the raw
    measurements and human readable ``warnings``. Callers decide what to do
    with ``reject``; ``loft`` already guarantees a single closed solid, so a
    rejection here means "closed, but folded".
    """
    face_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
    faces = [TopoDS.Face_s(face_map.FindKey(i)) for i in range(1, face_map.Extent() + 1)]
    planar = {i for i, face in enumerate(faces) if _surface_type(face) == 0}
    areas = [_area(face) for face in faces]

    edge_map = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_map)

    seam_angles = []
    open_edges = 0
    skipped = edge_map.Extent() > MAX_EDGES_FOR_SEAM_SWEEP
    if not skipped:
        for position in range(1, edge_map.Extent() + 1):
            adjacent = edge_map.FindFromIndex(position)
            if adjacent.Extent() != 2:
                open_edges += 1
                continue
            first = TopoDS.Face_s(adjacent.First())
            second = TopoDS.Face_s(adjacent.Last())
            i_first = face_map.FindIndex(first) - 1
            i_second = face_map.FindIndex(second) - 1
            # Cap boundaries are legitimately square; only judge band to band.
            if i_first in planar or i_second in planar:
                continue
            middle = _midpoint(TopoDS.Edge_s(edge_map.FindKey(position)))
            if middle is None:
                continue
            normal_first = _normal_at(first, middle)
            normal_second = _normal_at(second, middle)
            if normal_first is None or normal_second is None:
                continue
            dot = max(-1.0, min(1.0, float(np.dot(normal_first, normal_second))))
            seam_angles.append(math.degrees(math.acos(dot)))

    seam_angles = np.array(seam_angles, dtype=float)
    worst_seam = float(seam_angles.max()) if seam_angles.size else 0.0
    over_20 = int((seam_angles > 20.0).sum()) if seam_angles.size else 0
    min_area = min(areas) if areas else 0.0

    warnings = []
    status = 'ok'
    if skipped:
        status = 'warning'
        warnings.append('模型面数过多，已跳过接缝连续性抽检。')
    elif worst_seam > SEAM_REJECT_DEG:
        status = 'reject'
        warnings.append(
            '生成曲面出现折角：侧面接缝最大 %.1f°，超过限值 %.0f°。相邻截面被错位配对，模型不可用。'
            % (worst_seam, SEAM_REJECT_DEG))
    elif worst_seam > SEAM_WARN_DEG:
        status = 'warning'
        warnings.append('生成曲面侧面接缝最大 %.1f°，建议检查造型。' % worst_seam)

    if not skipped and over_20 and status == 'ok':
        status = 'warning'
        warnings.append('%d 条侧面接缝法线夹角超过 20°。' % over_20)

    if 0.0 < min_area < SLIVER_WARN_MM2:
        if status == 'ok':
            status = 'warning'
        warnings.append('存在面积仅 %.3f mm² 的薄片面，可能影响后续布尔运算。' % min_area)

    return {
        'status': status,
        'faces': len(faces),
        'solids': _count_solids(shape),
        'open_edges': open_edges,
        'volume_mm3': round(volume(shape), 3),
        'min_face_area_mm2': round(min_area, 4),
        'max_lateral_seam_deg': round(worst_seam, 3),
        'lateral_seams_over_20deg': over_20,
        'seam_check_skipped': skipped,
        'warnings': warnings,
    }
