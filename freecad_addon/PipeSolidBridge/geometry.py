# -*- coding: utf-8 -*-
"""Core geometry for PipeSolid Studio.

Turns a "stacked section" tube shell/solid into one smooth solid while keeping
two real parting edges on the part's mirror-symmetry sides.
Runs inside FreeCAD (Part module only, no GUI).
"""
import math
import time

import Part
from FreeCAD import Vector
import numpy as np

TWO_PI = 2.0 * math.pi


def _log(msg):
    print("[core] " + str(msg), flush=True)


def load_shape(path):
    sh = Part.Shape()
    sh.read(path)
    return sh


def section_edges(shape):
    """Closed edges = the section rings of a stacked-section shell/solid."""
    rings = [e for e in shape.Edges if e.isClosed()]
    if len(rings) < 3:
        raise ValueError("找到的闭合截面环少于 3 个，无法重建光滑实体")
    centers = [e.CenterOfMass for e in rings]
    ext = [max(c[i] for c in centers) - min(c[i] for c in centers) for i in range(3)]
    spine = max(range(3), key=lambda i: ext[i])
    rings.sort(key=lambda e: e.CenterOfMass[spine])
    return rings, spine


def _newell_normal(pts):
    n = Vector(0, 0, 0)
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        n.x += (p.y - q.y) * (p.z + q.z)
        n.y += (p.z - q.z) * (p.x + q.x)
        n.z += (p.x - q.x) * (p.y + q.y)
    if n.Length < 1e-12:
        raise ValueError("截面法向退化")
    n.normalize()
    return n


def section_frame(edge, samples=360):
    fp, lp = edge.FirstParameter, edge.LastParameter
    pts = [edge.valueAt(fp + (lp - fp) * k / samples) for k in range(samples)]
    n = _newell_normal(pts)
    c = Vector(0, 0, 0)
    for p in pts:
        c += p
    c /= len(pts)
    return n, c, fp, lp


def _proj(edge, t, c, a):
    return (edge.valueAt(t) - c).dot(a)


def _golden_extreme(edge, t0, c, a, fp, lp, sign, span):
    lo, hi = t0 - span, t0 + span
    gr = (math.sqrt(5.0) - 1.0) / 2.0

    def f(t):
        return sign * _proj(edge, t, c, a)

    c1 = hi - gr * (hi - lo)
    c2 = lo + gr * (hi - lo)
    f1, f2 = f(c1), f(c2)
    for _ in range(45):
        if f1 < f2:
            lo, c1, f1 = c1, c2, f2
            c2 = lo + gr * (hi - lo)
            f2 = f(c2)
        else:
            hi, c2, f2 = c2, c1, f1
            c1 = hi - gr * (hi - lo)
            f1 = f(c1)
    return (lo + hi) / 2.0


def parting_params(edge, direction):
    n, c, fp, lp = section_frame(edge)
    d = Vector(direction)
    a = d - n * d.dot(n)
    if a.Length < 1e-7:
        raise ValueError("分型方向与截面法向平行，无法投影")
    a.normalize()
    K = 720
    smax = (-1e30, fp)
    smin = (1e30, fp)
    per = lp - fp
    for k in range(K):
        t = fp + per * k / K
        s = _proj(edge, t, c, a)
        if s > smax[0]:
            smax = (s, t)
        if s < smin[0]:
            smin = (s, t)
    span = per / K
    t_plus = _golden_extreme(edge, smax[1], c, a, fp, lp, 1.0, span)
    t_minus = _golden_extreme(edge, smin[1], c, a, fp, lp, -1.0, span)
    t_plus = fp + ((t_plus - fp) % per)
    t_minus = fp + ((t_minus - fp) % per)
    return t_plus, t_minus, n, c, a, fp, lp


def build_section_curve(edge, direction, half_points=48):
    """Periodic BSpline through the ring; U=0 at +parting, U=0.5 at -parting."""
    t_plus, t_minus, n, c, a, fp, lp = parting_params(edge, direction)
    per = lp - fp
    M = int(half_points)
    if M < 8:
        raise ValueError("half_points 太小")
    t_minus2 = t_minus + per if t_minus <= t_plus else t_minus

    pts, pars = [], []
    for i in range(M):
        t = t_plus + (t_minus2 - t_plus) * i / (M - 1)
        pts.append(edge.valueAt(fp + ((t - fp) % per)))
        pars.append(0.5 * i / (M - 1))
    for i in range(1, M):
        t = t_minus2 + (t_plus + per - t_minus2) * i / M
        pts.append(edge.valueAt(fp + ((t - fp) % per)))
        pars.append(0.5 + 0.5 * i / M)
    pars.append(1.0)

    cr = Part.BSplineCurve()
    cr.interpolate(Points=pts, PeriodicFlag=True, Parameters=pars)
    return cr


def build_sections(rings, direction, half_points=48):
    return [build_section_curve(e, direction, half_points) for e in rings]


def build_smooth_surface(curves):
    bs = Part.BSplineSurface()
    bs.buildFromNSections(curves)
    return bs


def _symmetry_cloud(rings, nsections=12, samples=96):
    idxs = np.linspace(0, len(rings) - 1, min(nsections, len(rings))).astype(int)
    pts = []
    for i in idxs:
        e = rings[int(i)]
        fp, lp = e.FirstParameter, e.LastParameter
        for k in range(samples):
            p = e.valueAt(fp + (lp - fp) * k / samples)
            pts.append((p.x, p.y, p.z))
    return np.asarray(pts, float)


def auto_direction_scores(rings):
    """Mirror-symmetry score for the global X/Y/Z planes.

    A tube is mirrored about the plane that contains its (possibly bent) axis;
    the normal of that plane is the correct parting axis.  The score is the
    mean distance from reflected surface samples to the original point cloud,
    normalised by the tube radius.  Lower is better.
    """
    pts = _symmetry_cloud(rings)
    if len(pts) < 8:
        return [1.0, 1.0, 1.0]
    ctr = pts.mean(axis=0)
    scale = float(np.sqrt(((pts - ctr) ** 2).sum(axis=1)).mean()) or 1.0
    scores = []
    for axis in range(3):
        d = np.zeros(3)
        d[axis] = 1.0
        off = float(ctr[axis])
        refl = pts - 2.0 * np.outer((pts - off * d) @ d, d)
        best = np.empty(len(refl))
        for i in range(0, len(refl), 128):
            chunk = refl[i:i + 128]
            diff = chunk[:, None, :] - pts[None, :, :]
            best[i:i + len(chunk)] = np.sqrt((diff * diff).sum(axis=2)).min(axis=1)
        scores.append(float(best.mean() / scale))
    return scores


def pick_symmetry_axis(scores):
    """Lowest score; prefer Z, then Y, then X when the errors are equivalent."""
    best = min(range(3), key=lambda i: scores[i])
    for preferred in (2, 1, 0):
        if abs(scores[preferred] - scores[best]) < 1e-4:
            return preferred
    return best


def _unique_faces(shapes):
    out = []
    for s in shapes:
        if s.ShapeType == "Face":
            cand = [s]
        elif s.ShapeType in ("Shell", "Solid", "Compound", "CompSolid"):
            cand = list(s.Faces)
        else:
            continue
        for f in cand:
            if not any(f.isSame(g) for g in out):
                out.append(f)
    return out


def _as_shell(faces):
    sh = Part.makeShell(faces)
    if sh.ShapeType == "Shell":
        return sh
    # try sewing whatever came back, then extract one closed shell
    try:
        sh.sewShape()
    except Exception:
        pass
    if sh.ShapeType == "Shell":
        return sh
    shells = list(sh.Shells) if hasattr(sh, "Shells") else []
    for s in shells:
        if s.isClosed():
            return s
    if len(shells) == 1:
        return shells[0]
    raise ValueError("缝合失败：无法得到单一封闭壳")


def select_sections(rings, max_sections=40):
    """Subsample the section list to a stable count for the surface fit."""
    n = len(rings)
    if n <= max_sections:
        return list(rings)
    stride = int(math.ceil((n - 1) / float(max_sections - 1)))
    sub = list(rings[::stride])
    if sub[-1] is not rings[-1]:
        sub.append(rings[-1])
    return sub


def _build_once(all_rings, direction, half_points, max_sections, source_volume):
    t_all = time.time()
    rings = select_sections(all_rings, max_sections)
    curves = build_sections(rings, direction, half_points)
    t_sec = time.time()
    bs = build_smooth_surface(curves)
    shape = bs.toShape()
    face = max(shape.Faces, key=lambda f: f.Area)
    iso_edge = Part.Edge(bs.uIso(0.5))
    t_surf = time.time()

    r = Part.makeSplitShape(face, [(iso_edge, face)])
    faces = _unique_faces(list(r[0]) + list(r[1]))
    faces = [f for f in faces if f.Area > 1e-6]
    if len(faces) < 2:
        raise ValueError("分型线切割失败：未得到两个半面（%d）" % len(faces))
    faces.sort(key=lambda f: f.Area, reverse=True)
    half_faces = faces[:2]
    t_split = time.time()

    caps = [Part.Face(Part.Wire([Part.Edge(curves[0])])),
            Part.Face(Part.Wire([Part.Edge(curves[-1])]))]
    shell = _as_shell(half_faces + caps)
    if not shell.isClosed():
        raise ValueError("缝合后的壳未闭合")
    solid = Part.makeSolid(shell)
    if not solid.isValid():
        try:
            solid.fix(1e-5, 1e-5, 1e-5)
        except Exception:
            pass
    if len(solid.Solids) != 1:
        raise ValueError("结果不是单一实体（%d 个 solid）" % len(solid.Solids))
    if solid.Volume < 0:
        solid.reverse()

    d = Vector(direction)
    d.normalize()
    length = max(solid.BoundBox.XLength, solid.BoundBox.YLength, solid.BoundBox.ZLength)
    long_edges = [e for e in solid.Edges if e.BoundBox.DiagonalLength > 0.6 * length]
    t_done = time.time()

    info = {
        "sections": len(rings),
        "sections_total": len(all_rings),
        "half_points": int(half_points),
        "faces": len(solid.Faces),
        "edges": len(solid.Edges),
        "volume": float(solid.Volume),
        "source_volume": float(source_volume) if source_volume else None,
        "volume_error_pct": (100.0 * (solid.Volume - source_volume) / source_volume) if source_volume else None,
        "valid": bool(solid.isValid()),
        "long_edges": len(long_edges),
        "parting_dir": [float(d.x), float(d.y), float(d.z)],
        "bbox": [float(v) for v in (solid.BoundBox.XMin, solid.BoundBox.YMin, solid.BoundBox.ZMin,
                                    solid.BoundBox.XMax, solid.BoundBox.YMax, solid.BoundBox.ZMax)],
        "timings": {
            "sections": round(t_sec - t_all, 2),
            "surface": round(t_surf - t_sec, 2),
            "split": round(t_split - t_surf, 2),
            "assemble": round(t_done - t_split, 2),
            "total": round(t_done - t_all, 2),
        },
    }
    return solid, info


def build_solid(rings, direction, half_points=96, source_volume=None, max_sections=None,
                source_bbox=None):
    """Main pipeline with adaptive section count.

    Fits a C2 surface through reparameterised sections, splits it at the two
    parting meridians, caps the ends and returns a single closed solid.
    """
    all_rings = list(rings)
    if max_sections is not None:
        return _build_once(all_rings, direction, half_points, max_sections, source_volume)

    tol = 1.0
    if source_bbox is not None:
        diag = math.sqrt(sum((source_bbox[i + 3] - source_bbox[i]) ** 2 for i in range(3)))
        tol = max(1.0, 0.0025 * diag)
    best = None
    attempts = []
    for ms in (40, 64, 96, 160):
        try:
            solid, info = _build_once(all_rings, direction, half_points, ms, source_volume)
        except Exception as exc:
            attempts.append({"max_sections": ms, "error": str(exc)})
            continue
        if source_bbox is not None:
            bb = info["bbox"]
            dev = max(abs(bb[i] - source_bbox[i]) for i in range(6))
        else:
            dev = 0.0
        info["bbox_dev"] = round(float(dev), 3)
        attempts.append({"sections": info["sections"], "bbox_dev": round(float(dev), 2),
                         "volume": round(info["volume"], 1)})
        if best is None or dev < best[0]:
            best = (dev, solid, info)
        vol_ok = source_volume is None or abs(info["volume_error_pct"] or 0.0) <= 1.0
        if dev <= tol and vol_ok:
            info["attempts"] = attempts
            return solid, info
    if best is None:
        raise ValueError("光滑实体构建失败：" + "; ".join(str(a) for a in attempts))
    info = best[2]
    info["attempts"] = attempts
    return best[1], info

