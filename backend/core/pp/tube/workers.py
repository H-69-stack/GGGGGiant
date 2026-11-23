"""Top-level worker functions for multiprocessing.

Functions defined here must be pickleable, so they cannot live inside a
class or capture closures. They take the heavy mesh/shape as the first
argument and a chunk of work items (Z values, centerline points, etc.).
"""

from __future__ import annotations

import io
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .geometry import (
    hull_and_inner,
    orthonormal_basis,
    polygon_centroid,
    polygon_perimeter,
)


def _load_mesh_from_bytes(mesh_bytes: bytes):
    """trimesh.load needs a file-like object when a buffer is passed."""
    import trimesh

    mesh = trimesh.load(io.BytesIO(mesh_bytes), file_type="stl")
    if isinstance(mesh, trimesh.Scene):
        geoms = list(getattr(mesh, "geometry", {}).values())
        if not geoms:
            return None
        mesh = trimesh.util.concatenate(geoms)
    return mesh


def _section_polylines_2d(section, u: np.ndarray, v: np.ndarray, origin: np.ndarray) -> Tuple[List[np.ndarray], np.ndarray]:
    """Extract closed polylines from a trimesh ``Path3D`` cross-section.

    Returns (polylines_2d, all_points_2d). Each polyline is an (N, 2)
    array in the (u, v) frame relative to ``origin``. all_points_2d is
    the concatenation, used as a fallback for the convex hull.
    """
    polylines: List[np.ndarray] = []
    try:
        # `discrete` is a list of (N, 3) arrays — one per closed polyline.
        for poly3d in section.discrete:
            poly3d = np.asarray(poly3d, dtype=float)
            if poly3d.shape[0] < 3:
                continue
            rel = poly3d - origin
            poly2d = np.column_stack([rel @ u, rel @ v])
            polylines.append(poly2d)
    except Exception:
        polylines = []

    if not polylines and section.vertices is not None and len(section.vertices) >= 3:
        verts3d = np.asarray(section.vertices, dtype=float)
        rel = verts3d - origin
        all2d = np.column_stack([rel @ u, rel @ v])
        return [], all2d

    if polylines:
        return polylines, np.vstack(polylines)
    return [], np.zeros((0, 2), dtype=float)


# ─────────────────────────── STL workers ───────────────────────────

def stl_slice_z_chunk(args) -> List[dict]:
    """Slice a trimesh.Trimesh at multiple Z heights (plane normal = Z).

    args = (mesh_bytes, z_values)
    Returns a list of dicts (one per successful slice).
    """
    mesh_bytes, z_values = args
    mesh = _load_mesh_from_bytes(mesh_bytes)
    if mesh is None:
        return []

    out: List[dict] = []
    plane_normal = np.array([0.0, 0.0, 1.0])
    u_axis = np.array([1.0, 0.0, 0.0])
    v_axis = np.array([0.0, 1.0, 0.0])
    for z in z_values:
        try:
            origin = np.array([0.0, 0.0, float(z)], dtype=float)
            section = mesh.section(plane_origin=origin, plane_normal=plane_normal)
            if section is None:
                continue
            polylines, all_pts = _section_polylines_2d(section, u_axis, v_axis, origin)
            if not polylines and len(all_pts) < 3:
                continue
            data = hull_and_inner(all_pts if len(all_pts) >= 3 else None, polylines_2d=polylines)
            if data is None:
                continue
            outer = data["outer_2d"]
            inner = data["inner_2d"]
            cx, cy = data["centroid"]
            outer_centered = outer - np.array([cx, cy])
            inner_centered = (inner - np.array([cx, cy])) if inner is not None else None
            out.append({
                "z": float(z),
                "centroid_2d": [cx, cy],
                "perimeter": data["perimeter"],
                "wall_thickness": data["wall_thickness"],
                "outer_2d": outer_centered.tolist(),
                "inner_2d": inner_centered.tolist() if inner_centered is not None else None,
            })
        except Exception:
            continue
    return out


def stl_secondary_cut_chunk(args) -> List[dict]:
    """Compute perpendicular cross-sections at the given centerline points.

    args = (mesh_bytes, [(point_xyz, tangent_xyz, idx), ...])
    Returns list of dicts keyed by original index.
    """
    mesh_bytes, items = args
    mesh = _load_mesh_from_bytes(mesh_bytes)
    if mesh is None:
        return []

    out: List[dict] = []
    for (point, tangent, idx) in items:
        try:
            point_arr = np.asarray(point, dtype=float)
            tangent_arr = np.asarray(tangent, dtype=float)
            section = mesh.section(plane_origin=point_arr, plane_normal=tangent_arr)
            if section is None:
                continue

            _, u, v = orthonormal_basis(tangent_arr)
            polylines, all_pts = _section_polylines_2d(section, u, v, point_arr)
            if not polylines and len(all_pts) < 3:
                continue

            data = hull_and_inner(all_pts if len(all_pts) >= 3 else None, polylines_2d=polylines)
            if data is None:
                continue

            outer = data["outer_2d"]
            inner = data["inner_2d"]
            cx, cy = data["centroid"]

            outline_3d = (outer[:, [0]] * u + outer[:, [1]] * v) + point_arr

            outer_centered = outer - np.array([cx, cy])
            inner_centered = (inner - np.array([cx, cy])) if inner is not None else None

            out.append({
                "idx": int(idx),
                "perimeter": data["perimeter"],
                "wall_thickness": data["wall_thickness"],
                "centroid_local": [0.0, 0.0],
                "outer_2d": outer_centered.tolist(),
                "inner_2d": inner_centered.tolist() if inner_centered is not None else None,
                "outline_3d": outline_3d.tolist(),
                "point": point_arr.tolist(),
                "tangent": tangent_arr.tolist(),
            })
        except Exception:
            continue
    return out


# ─────────────────────────── STP workers ───────────────────────────
# STP processing is delegated to `section_worker.py` via subprocess.
# The functions below are placeholders so future in-process workers can
# share the same dispatcher.
