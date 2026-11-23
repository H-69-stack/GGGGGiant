"""Geometry helpers shared by A/B algorithms.

Everything here works on plain numpy arrays so it can be reused by both
the STL engine (which uses trimesh) and the STP engine (which uses OCP)
without coupling them to the heavy CAD libraries.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.spatial import ConvexHull


# ─────────────────────────── Rotation ───────────────────────────

def rotation_between_vectors(vec_from: np.ndarray, vec_to: np.ndarray) -> np.ndarray:
    """Return a 3x3 rotation matrix that maps `vec_from` onto `vec_to`."""
    a = np.asarray(vec_from, dtype=float)
    b = np.asarray(vec_to, dtype=float)
    a = a / max(np.linalg.norm(a), 1e-12)
    b = b / max(np.linalg.norm(b), 1e-12)

    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if np.linalg.norm(v) < 1e-10:
        if c > 0:
            return np.eye(3)
        # 180° rotation; pick any axis perpendicular to a
        perp = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        perp = perp - np.dot(perp, a) * a
        perp = perp / np.linalg.norm(perp)
        return 2.0 * np.outer(perp, perp) - np.eye(3)

    vx = np.array([
        [0.0, -v[2], v[1]],
        [v[2], 0.0, -v[0]],
        [-v[1], v[0], 0.0],
    ])
    return np.eye(3) + vx + vx @ vx / (1.0 + c)


def pca_long_axis(points: np.ndarray) -> np.ndarray:
    centered = points - points.mean(axis=0)
    cov = np.cov(centered.T)
    _, vecs = np.linalg.eigh(cov)
    axis = vecs[:, -1]
    return axis / np.linalg.norm(axis)


def principal_extents_2d(points) -> Optional[Tuple[float, float]]:
    """Return (length, width) as principal-axis extents of a 2D contour.

    Drawing oval sizes are the ellipse axes, not an axis-aligned bounding box
    in an arbitrary local frame. SVD extents stay stable under in-plane rotation.
    """
    if points is None:
        return None
    try:
        arr = np.asarray(points, dtype=float)
    except (TypeError, ValueError):
        return None
    if arr.ndim != 2 or arr.shape[0] < 3 or arr.shape[1] < 2:
        return None
    arr = arr[:, :2]
    finite = np.isfinite(arr).all(axis=1)
    if not np.all(finite):
        arr = arr[finite]
        if arr.shape[0] < 3:
            return None
    centered = arr - arr.mean(axis=0)
    try:
        _, _, vt = np.linalg.svd(centered, full_matrices=False)
    except np.linalg.LinAlgError:
        return None
    if vt.shape[0] < 2:
        return None
    uv = centered @ vt[:2].T
    a = float(uv[:, 0].max() - uv[:, 0].min())
    b = float(uv[:, 1].max() - uv[:, 1].min())
    if not (np.isfinite(a) and np.isfinite(b)) or a <= 0 or b <= 0:
        return None
    return (max(a, b), min(a, b))


# ─────────────────────────── Convex hull primitives ───────────────────────────

def polygon_centroid(verts_2d: np.ndarray) -> Tuple[float, float]:
    """Shoelace-formula centroid of a simple 2D polygon."""
    if len(verts_2d) == 0:
        return 0.0, 0.0
    x = verts_2d[:, 0]
    y = verts_2d[:, 1]
    x1 = np.roll(x, -1)
    y1 = np.roll(y, -1)
    cross = x * y1 - x1 * y
    area_sum = float(np.sum(cross))
    if abs(area_sum) < 1e-12:
        return float(np.mean(x)), float(np.mean(y))
    cx = float(np.sum((x + x1) * cross)) / (3.0 * area_sum)
    cy = float(np.sum((y + y1) * cross)) / (3.0 * area_sum)
    return cx, cy


def polygon_perimeter(verts_2d: np.ndarray) -> float:
    diffs = np.roll(verts_2d, -1, axis=0) - verts_2d
    return float(np.sum(np.linalg.norm(diffs, axis=1)))


def _polygon_signed_area(verts: np.ndarray) -> float:
    if len(verts) < 3:
        return 0.0
    x = verts[:, 0]
    y = verts[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _point_in_polygon(point: np.ndarray, polygon: np.ndarray) -> bool:
    """Standard ray-casting point-in-polygon test."""
    if len(polygon) < 3:
        return False
    x, y = float(point[0]), float(point[1])
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = float(polygon[i, 0]), float(polygon[i, 1])
        xj, yj = float(polygon[j, 0]), float(polygon[j, 1])
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-30) + xi
        ):
            inside = not inside
        j = i
    return inside


def _looks_closed(poly: np.ndarray) -> bool:
    if len(poly) < 4:
        return False
    return bool(np.linalg.norm(poly[0] - poly[-1]) < 1e-6)


def hull_and_inner(
    pts_2d: np.ndarray,
    polylines_2d: Optional[Sequence[np.ndarray]] = None,
) -> Optional[dict]:
    """Compute outer/inner polygons and wall thickness for a cross-section.

    Preferred path (when ``polylines_2d`` is supplied): treat each closed
    polyline as a candidate ring, take the largest by area as the outer
    ring, and any polyline that lies *inside* the outer ring as inner
    ring candidates (their convex hull is the inner wall).

    Fallback (only ``pts_2d``): use the convex hull as outer and try to
    rebuild an inner ring from the remaining points (legacy behaviour).
    Returns dict with keys outer_2d, inner_2d, perimeter, centroid,
    wall_thickness.
    """
    outer: Optional[np.ndarray] = None
    inner: Optional[np.ndarray] = None
    perimeter_value: Optional[float] = None

    if polylines_2d:
        rings: List[Tuple[float, np.ndarray]] = []
        for poly in polylines_2d:
            if poly is None or len(poly) < 4:
                continue
            verts = np.asarray(poly, dtype=float)
            if _looks_closed(verts):
                verts = verts[:-1]
            if len(verts) < 3:
                continue
            area = abs(_polygon_signed_area(verts))
            if area < 1e-6:
                continue
            rings.append((area, verts))

        if rings:
            rings.sort(key=lambda r: r[0], reverse=True)
            outer = rings[0][1]
            perimeter_value = polygon_perimeter(outer)

            inner_candidates: List[np.ndarray] = []
            for _, ring in rings[1:]:
                centroid_pt = ring.mean(axis=0)
                if _point_in_polygon(centroid_pt, outer):
                    inner_candidates.append(ring)
            if inner_candidates:
                inner_pts = np.vstack(inner_candidates)
                try:
                    hull = ConvexHull(inner_pts)
                    inner = inner_pts[hull.vertices]
                except Exception:
                    inner = None

    if outer is None:
        if pts_2d is None or len(pts_2d) < 3:
            return None
        try:
            hull = ConvexHull(pts_2d)
        except Exception:
            return None
        outer = pts_2d[hull.vertices]
        perimeter_value = polygon_perimeter(outer)
        outer_set = set(hull.vertices.tolist())
        inner_idx = [i for i in range(len(pts_2d)) if i not in outer_set]
        if len(inner_idx) >= 3:
            try:
                inner_pts = pts_2d[inner_idx]
                inner_hull = ConvexHull(inner_pts)
                inner = inner_pts[inner_hull.vertices]
            except Exception:
                inner = None

    cx, cy = polygon_centroid(outer)
    wall_thickness = (
        _wall_thickness_radial(outer, inner, (cx, cy)) if inner is not None else None
    )

    return {
        "outer_2d": outer,
        "inner_2d": inner,
        "perimeter": float(perimeter_value if perimeter_value is not None else 0.0),
        "centroid": (cx, cy),
        "wall_thickness": wall_thickness,
    }


def _wall_thickness_radial(
    outer_2d: np.ndarray,
    inner_2d: np.ndarray,
    center: Tuple[float, float],
    n_angles: int = 144,
) -> Optional[float]:
    """Median radial distance between outer and inner wall.

    Samples ``n_angles`` rays from ``center``; for each ray we take the
    farthest projection on the outer polygon and the farthest projection
    on the inner polygon, then return the median radial difference.
    """
    if inner_2d is None or len(inner_2d) < 3:
        return None
    cx, cy = center
    pts_o = outer_2d - np.array([cx, cy])
    pts_i = inner_2d - np.array([cx, cy])
    angles = np.linspace(0.0, 2.0 * np.pi, n_angles, endpoint=False)
    dirs = np.column_stack([np.cos(angles), np.sin(angles)])
    proj_o = pts_o @ dirs.T
    proj_i = pts_i @ dirs.T
    outer_r = np.max(proj_o, axis=0)
    inner_r = np.max(proj_i, axis=0)
    valid = (outer_r > 0) & (inner_r > 0) & (outer_r > inner_r)
    if not np.any(valid):
        return None
    diff = outer_r[valid] - inner_r[valid]
    return float(np.median(diff))


# ─────────────────────────── Cumulative arc length ───────────────────────────

def cumulative_lengths(points: Sequence[Sequence[float]]) -> List[float]:
    if not points:
        return []
    arr = np.asarray(points, dtype=float)
    cum = [0.0]
    for i in range(1, len(arr)):
        cum.append(cum[-1] + float(np.linalg.norm(arr[i] - arr[i - 1])))
    return cum


# ─────────────────────────── Smoothing & extrapolation ───────────────────────────

def trim_and_extrapolate_centerline(
    positions: np.ndarray,
    trim: int = 10,
    n_fit: int = 5,
    stations: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Replace endpoint XYZ using interior linear trends at unchanged stations.

    Unreliable original endpoint coordinates must not anchor the new line.
    The number of points and the caller's station/length array are unchanged.
    """
    n = len(positions)
    if n <= trim * 2 + n_fit + 2:
        return positions

    out = np.array(positions, dtype=float, copy=True)
    s = np.arange(n, dtype=float) if stations is None else np.asarray(stations, float)
    if len(s) != n or not np.all(np.diff(s) > 0):
        raise ValueError('中心线站位必须严格递增且与中心点一一对应')
    for anchor, fit_indices, targets in ((trim, np.arange(trim,trim+n_fit),np.arange(trim)),
                                       (n-trim-1,np.arange(n-trim-n_fit,n-trim),np.arange(n-trim,n))):
        x = s[fit_indices]-s[anchor]
        slope = np.tensordot(x, out[fit_indices]-out[anchor], axes=1)/float(x@x)
        out[targets] = out[anchor]+(s[targets]-s[anchor])[:,None]*slope
    return out


def smooth_centerline_savgol(points: np.ndarray, window: int = 11, poly: int = 3) -> np.ndarray:
    """Apply Savitzky–Golay smoothing to each coordinate of the polyline.

    Falls back to a simple moving average if scipy is missing.
    """
    n = len(points)
    if n < 5:
        return points
    try:
        from scipy.signal import savgol_filter
    except Exception:
        kernel_size = max(3, min(window, n if n % 2 == 1 else n - 1))
        if kernel_size % 2 == 0:
            kernel_size -= 1
        if kernel_size < 3:
            return points
        kernel = np.ones(kernel_size) / kernel_size
        out = np.zeros_like(points)
        for dim in range(points.shape[1]):
            out[:, dim] = np.convolve(points[:, dim], kernel, mode="same")
        return out

    w = min(window, n if n % 2 == 1 else n - 1)
    if w < poly + 2:
        return points
    if w % 2 == 0:
        w -= 1
    out = np.zeros_like(points)
    for dim in range(points.shape[1]):
        out[:, dim] = savgol_filter(points[:, dim], w, poly)
    return out


def trim_and_extrapolate_series(
    values: np.ndarray,
    trim: int = 10,
    n_fit: int = 10,
) -> np.ndarray:
    """Replace the first/last `trim` samples of a 1D series with values that
    follow the local linear trend of the next `n_fit` interior samples.
    """
    n = len(values)
    if n <= trim * 2 + n_fit + 2:
        return values
    out = np.array(values, dtype=float, copy=True)
    idx = np.arange(n, dtype=float)

    head_x = idx[trim: trim + n_fit]
    head_y = out[trim: trim + n_fit]
    head_coef = np.polyfit(head_x, head_y, deg=1)
    out[:trim] = np.polyval(head_coef, idx[:trim])

    tail_x = idx[n - trim - n_fit: n - trim]
    tail_y = out[n - trim - n_fit: n - trim]
    tail_coef = np.polyfit(tail_x, tail_y, deg=1)
    out[n - trim:] = np.polyval(tail_coef, idx[n - trim:])
    return out


# ─────────────────────────── Sparse sampling by arc length ───────────────────────────

def sparse_indices_by_arc_length(
    cum_lengths: Sequence[float],
    step_mm: float = 25.0,
    include_endpoints: bool = True,
) -> List[int]:
    """Pick indices into `cum_lengths` so that consecutive selected points are
    roughly `step_mm` apart along the centerline.
    """
    if len(cum_lengths) == 0:
        return []
    total = float(cum_lengths[-1])
    if total <= 0:
        return [0]

    if step_mm <= 0:
        step_mm = 25.0

    selected = [0]
    next_target = step_mm
    last_idx = 0
    arr = np.asarray(cum_lengths, dtype=float)
    while next_target < total:
        idx = int(np.searchsorted(arr, next_target, side="left"))
        idx = min(idx, len(arr) - 1)
        if idx != last_idx:
            selected.append(idx)
            last_idx = idx
        next_target += step_mm

    if include_endpoints and selected[-1] != len(arr) - 1:
        selected.append(len(arr) - 1)
    return selected


# ─────────────────────────── Local frames along centerline ───────────────────────────

def centerline_tangents(points: np.ndarray) -> np.ndarray:
    """Per-point unit tangents using central differences (forward/backward at ends)."""
    n = len(points)
    tangents = np.zeros_like(points)
    if n < 2:
        return tangents
    for i in range(n):
        if i == 0:
            d = points[1] - points[0]
        elif i == n - 1:
            d = points[-1] - points[-2]
        else:
            d = points[i + 1] - points[i - 1]
        norm = float(np.linalg.norm(d))
        tangents[i] = d / norm if norm > 1e-12 else np.array([0.0, 0.0, 1.0])
    return tangents


def orthonormal_basis(normal: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (n_unit, u, v) where u, v span the plane perpendicular to `normal`."""
    n = np.asarray(normal, dtype=float)
    n = n / max(np.linalg.norm(n), 1e-12)
    helper = np.array([1.0, 0.0, 0.0]) if abs(n[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(n, helper)
    u = u / max(np.linalg.norm(u), 1e-12)
    v = np.cross(n, u)
    v = v / max(np.linalg.norm(v), 1e-12)
    return n, u, v
