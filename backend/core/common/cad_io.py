"""STEP I/O via OpenCASCADE (cadquery-ocp).

Two export paths share one lazy OCP importer:

  * ``rings_to_step_bspline_bytes`` — a *truly* parametric STEP from a stack
    of 3D rings. Builds a periodic cubic B-spline curve through each ring,
    lofts a B-spline surface via ``BRepOffsetAPI_ThruSections`` (``is_solid``
    chooses capped solid vs. open hollow shell), and writes it through
    ``STEPControl_Writer``. Output is a few KB for a whole revolved mold —
    continuous-curvature surfaces, no facets, no orange-peel.

  * ``trimesh_to_step_bytes`` — a CAD-loadable B-rep STEP from a plain
    ``trimesh.Trimesh``: one planar ``TopoDS_Face`` per triangle inside a
    ``TopoDS_Compound``. Opens cleanly in SolidWorks / Fusion 360 / FreeCAD /
    CATIA, but is large for big meshes (~500 bytes/face).

Callers should prefer the NURBS path when aligned 3D rings are available
(revolved surfaces, lofted tubes) and fall back to the triangular path
otherwise — never to the deleted hand-written POLYLOOP exporter.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


def _import_ocp():
    """Lazy-import the OpenCASCADE bindings; returns ``None`` if missing.

    A single importer serves both the NURBS path and the triangular-face
    path — OCP is a monolithic install, so the symbol sets are either all
    present or all absent, and one dict lets callers pay only for what they
    index. Keeping this lazy keeps unit tests on hosts without
    ``cadquery-ocp`` runnable.
    """
    try:
        from OCP.gp import gp_Pnt
        from OCP.TColgp import TColgp_HArray1OfPnt
        from OCP.GeomAPI import GeomAPI_Interpolate
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeWire,
            BRepBuilderAPI_MakePolygon,
            BRepBuilderAPI_MakeFace,
        )
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound
        from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
        from OCP.IFSelect import IFSelect_RetDone
        return {
            "gp_Pnt": gp_Pnt,
            "TColgp_HArray1OfPnt": TColgp_HArray1OfPnt,
            "GeomAPI_Interpolate": GeomAPI_Interpolate,
            "BRepBuilderAPI_MakeEdge": BRepBuilderAPI_MakeEdge,
            "BRepBuilderAPI_MakeWire": BRepBuilderAPI_MakeWire,
            "BRepBuilderAPI_MakePolygon": BRepBuilderAPI_MakePolygon,
            "BRepBuilderAPI_MakeFace": BRepBuilderAPI_MakeFace,
            "BRep_Builder": BRep_Builder,
            "TopoDS_Compound": TopoDS_Compound,
            "BRepOffsetAPI_ThruSections": BRepOffsetAPI_ThruSections,
            "STEPControl_Writer": STEPControl_Writer,
            "STEPControl_AsIs": STEPControl_AsIs,
            "IFSelect_RetDone": IFSelect_RetDone,
        }
    except Exception as exc:  # pragma: no cover - module level
        logger.debug("OCP 不可用，STEP 导出无法使用: %s", exc)
        return None


def _subsample(arr: np.ndarray, step: int) -> np.ndarray:
    """Keep every ``step``-th row, always preserving first and last."""
    if step <= 1 or arr.shape[0] <= 2:
        return arr
    keep = list(range(0, arr.shape[0], step))
    if keep[-1] != arr.shape[0] - 1:
        keep.append(arr.shape[0] - 1)
    return arr[keep]


def _build_periodic_bspline_wire(
    ring: np.ndarray,
    ocp,
    angular_step: int = 1,
    tol: float = 1e-4,
):
    """Build a ``TopoDS_Wire`` with one closed periodic B-spline edge."""
    pts = _subsample(np.asarray(ring, dtype=float), max(1, int(angular_step)))
    n = pts.shape[0]
    if n < 4:
        raise ValueError(f"ring needs at least 4 points, got {n}")

    arr = ocp["TColgp_HArray1OfPnt"](1, n)
    for i in range(n):
        arr.SetValue(i + 1, ocp["gp_Pnt"](float(pts[i, 0]), float(pts[i, 1]), float(pts[i, 2])))

    interp = ocp["GeomAPI_Interpolate"](arr, True, float(tol))  # True → periodic
    interp.Perform()
    if not interp.IsDone():
        raise RuntimeError("GeomAPI_Interpolate failed for ring")
    curve = interp.Curve()

    edge = ocp["BRepBuilderAPI_MakeEdge"](curve).Edge()
    wire = ocp["BRepBuilderAPI_MakeWire"](edge).Wire()
    return wire


def rings_to_step_bspline_bytes(
    rings_3d: Sequence[np.ndarray],
    *,
    axial_subsample: int = 1,
    angular_subsample: int = 1,
    is_ruled: bool = False,
    is_solid: bool = True,
    interpolation_tol: float = 1e-4,
) -> Optional[bytes]:
    """Build a NURBS solid/shell from stacked rings and return STEP file bytes.

    Args:
        rings_3d: ordered list of (n_angular, 3) arrays. Each ring is one
            closed cross-section; rings are stacked along the centerline.
            Index ``k`` must point to the same angular slot across all rings
            (which is what ``_resample_by_polar_angle`` guarantees).
        axial_subsample: keep every Nth ring before lofting. ThruSections
            then interpolates a continuous B-spline surface through them, so
            cutting density to e.g. 1/4 typically has no visible quality
            cost while making the STEP file 4× smaller and the build 4×
            faster. First and last ring are always kept.
        angular_subsample: same idea but along the ring perimeter. 1 keeps
            every angular sample; 2 halves it; etc.
        is_ruled: when True the loft is C0 between rings (ruled surface);
            when False (default) it is a single C2 B-spline surface — much
            smoother visually and usually preferred.
        is_solid: when True (default) ThruSections caps both ends → a closed
            solid (matches meshes built with end caps). When False it
            produces an open shell — for hollow surfaces of revolution
            (e.g. a TP draw-mold whose profile encloses the axis and has no
            end caps) so the bore stays open.
        interpolation_tol: tolerance for the per-ring B-spline interpolation
            in mm.

    Returns:
        STEP file bytes, or ``None`` if OCP is unavailable / the build
        fails. Callers should fall back to ``trimesh_to_step_bytes``.
    """
    ocp = _import_ocp()
    if ocp is None:
        return None
    if not rings_3d or len(rings_3d) < 2:
        return None

    rings = list(rings_3d)
    if int(axial_subsample) > 1:
        keep = list(range(0, len(rings), int(axial_subsample)))
        if keep[-1] != len(rings) - 1:
            keep.append(len(rings) - 1)
        rings = [rings[i] for i in keep]
    if len(rings) < 2:
        return None

    try:
        wires = []
        for r in rings:
            wires.append(_build_periodic_bspline_wire(
                r, ocp,
                angular_step=int(angular_subsample),
                tol=float(interpolation_tol),
            ))

        loft = ocp["BRepOffsetAPI_ThruSections"](bool(is_solid), bool(is_ruled))
        for w in wires:
            loft.AddWire(w)
        loft.Build()
        shape = loft.Shape()
        if shape.IsNull():
            return None

        # Write to a temporary STEP file then read back as bytes (the OCP
        # writer wants a path, not a file-like object).
        fd, path = tempfile.mkstemp(suffix=".step")
        os.close(fd)
        try:
            writer = ocp["STEPControl_Writer"]()
            status = writer.Transfer(shape, ocp["STEPControl_AsIs"])
            if status != ocp["IFSelect_RetDone"]:
                return None
            if writer.Write(path) != ocp["IFSelect_RetDone"]:
                return None
            with open(path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("NURBS STEP 导出失败，回退到三角面 STEP: %s", exc)
        return None


def trimesh_to_step_bytes(mesh, *, angular_step: int = 1) -> Optional[bytes]:
    """Build a STEP file from a ``trimesh.Trimesh`` via OpenCASCADE.

    Each triangle becomes a planar ``TopoDS_Face`` inside a single
    ``TopoDS_Compound``; the whole compound is written as a single STEP file
    using ``STEPControl_Writer``. Each face carries a ``PLANE`` surface and
    orientation, so the resulting STEP opens cleanly in SolidWorks / Fusion
    360 / FreeCAD / CATIA.

    Args:
        mesh: a ``trimesh.Trimesh`` instance.
        angular_step: subsample every Nth triangle before write — trades
            file size / generation speed against displayed resolution.
            Only the ``trimesh`` triangle list is sub-sampled; the geometry
            of each emitted face is preserved exactly. Leave at 1 for closed
            solids — dropping triangles opens holes in the surface.

    Returns:
        STEP file bytes, or ``None`` if OCP is unavailable / the build
        fails. Callers must surface the failure rather than fall back to a
        hand-written exporter: silently serving a malformed STEP is the bug
        we are fixing.
    """
    ocp = _import_ocp()
    if ocp is None:
        return None
    if mesh is None or len(mesh.faces) == 0:
        return None

    try:
        verts = mesh.vertices
        faces = mesh.faces[:: max(1, int(angular_step))]

        compound = ocp["TopoDS_Compound"]()
        builder = ocp["BRep_Builder"]()
        builder.MakeCompound(compound)

        gp = ocp["gp_Pnt"]
        MakePolygon = ocp["BRepBuilderAPI_MakePolygon"]
        MakeFace = ocp["BRepBuilderAPI_MakeFace"]

        skipped = 0
        for tri in faces:
            v0, v1, v2 = verts[tri[0]], verts[tri[1]], verts[tri[2]]
            poly = MakePolygon(
                gp(float(v0[0]), float(v0[1]), float(v0[2])),
                gp(float(v1[0]), float(v1[1]), float(v1[2])),
                gp(float(v2[0]), float(v2[1]), float(v2[2])),
            )
            poly.Close()
            if not poly.IsDone():
                skipped += 1
                continue
            wire = poly.Wire()
            face = MakeFace(wire).Face()
            if face.IsNull():
                skipped += 1
                continue
            builder.Add(compound, face)

        if skipped:
            logger.warning("trimesh_to_step_bytes: 跳过 %d 个退化三角面", skipped)

        fd, path = tempfile.mkstemp(suffix=".step")
        os.close(fd)
        try:
            writer = ocp["STEPControl_Writer"]()
            status = writer.Transfer(compound, ocp["STEPControl_AsIs"])
            if status != ocp["IFSelect_RetDone"]:
                logger.warning("STEPControl_Writer.Transfer 返回非完成状态: %s", status)
                return None
            if writer.Write(path) != ocp["IFSelect_RetDone"]:
                logger.warning("STEPControl_Writer.Write 返回非完成状态: %s", status)
                return None
            with open(path, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("三角面 STEP 导出失败: %s", exc)
        return None
