"""Build a clean solid from Giant-main-new1 measured section data.

The Giant cache stores every measured section as a 3D outline plus its local
frame.  That is much more reliable than the 137 hand-built strips in the STEP
files, and the end stations are explicitly marked extrapolated, so the fake
free end can be dropped before fitting.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.TColgp import TColgp_HArray1OfPnt
from OCP.gp import gp_Pnt

from .assemble import build_from_closed_sections
from .geometry import BuildOptions, Section, prepare_sections, write_step


def _polygon_edge(points: np.ndarray):
    arr = TColgp_HArray1OfPnt(1, len(points))
    for i, p in enumerate(points):
        arr.SetValue(i + 1, gp_Pnt(float(p[0]), float(p[1]), float(p[2])))
    interp = GeomAPI_Interpolate(arr, True, 1e-7)
    interp.Perform()
    if not interp.IsDone():
        raise ValueError("Giant 截面闭合曲线拟合失败")
    return BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()


def _smooth_loop(points: np.ndarray, passes: int = 2) -> np.ndarray:
    pts = points.copy()
    n = len(pts)
    for _ in range(passes):
        pts = 0.5 * pts + 0.25 * np.roll(pts, 1, axis=0) + 0.25 * np.roll(pts, -1, axis=0)
    return pts


def _normalise_frame(normal, basis_u, basis_v):
    n = np.asarray(normal, dtype=float)
    u = np.asarray(basis_u, dtype=float)
    v = np.asarray(basis_v, dtype=float)
    # Gram-Schmidt keeps the section plane and its local coordinates coherent
    n /= max(np.linalg.norm(n), 1e-12)
    u = u - np.dot(u, n) * n
    u /= max(np.linalg.norm(u), 1e-12)
    v = v - np.dot(v, n) * n - np.dot(v, u) * u
    v /= max(np.linalg.norm(v), 1e-12)
    return n, u, v


def load_giant_sections(
    cache_path: str | Path,
    step_mm: float = 2.0,
    half_samples: int = 65,
    smooth_passes: int = 2,
    keep_extrapolated: bool = False,
    keep_all_extrapolated: bool = False,
) -> list[Section]:
    data = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    source = data.get("sections_full") or data.get("sections") or []
    if len(source) < 5:
        raise ValueError("Giant 截面数据不足")
    # A repaired cache already contains the processor's chosen rings in order.
    # Do not re-sample or smooth them: that would discard the very rings that
    # fixed the section ordering.
    is_repaired = any(str(s.get("measurement_status", "")) == "repaired" for s in source)

    # Drop the explicitly extrapolated end stations.  They are the free /
    # fabricated end that must not be used as a hard loft constraint.
    measured = [s for s in source if keep_extrapolated or str(s.get("measurement_status", "measured")) != "extrapolated"]
    if len(measured) < 5:
        measured = source
    measured.sort(key=lambda s: float(s.get("cumulative_length", 0.0)))

    # drop duplicated stations; some caches contain several records at the
    # same arc length, which makes the loft fold
    dedup = []
    for s in measured:
        length = float(s.get("cumulative_length", 0.0))
        if not dedup or length - float(dedup[-1].get("cumulative_length", 0.0)) > 0.1:
            dedup.append(s)
    if dedup:
        measured = dedup

    if is_repaired:
        chosen = list(measured)
    else:
        chosen = []
        next_len = None
        last_center = None
        for s in measured:
            length = float(s.get("cumulative_length", 0.0))
            center = np.asarray(s["position"], dtype=float)
            if next_len is None or length >= next_len - 1e-9:
                if last_center is None or np.linalg.norm(center - last_center) >= 0.5:
                    chosen.append(s)
                    next_len = length + step_mm
                    last_center = center
        if measured and chosen:
            last = measured[-1]
            last_center = np.asarray(last["position"], dtype=float)
            chosen_center = np.asarray(chosen[-1]["position"], dtype=float)
            last_is_extrapolated = str(last.get("measurement_status", "measured")) == "extrapolated"
            if (np.linalg.norm(last_center - chosen_center) > 1e-9 and
                    (last_is_extrapolated or np.linalg.norm(last_center - chosen_center) > 0.5)):
                chosen.append(last)

    # local moving average of the centres; translate the outline with the
    # centre so the measured cross-section shape is preserved.
    centers = np.array([s["position"] for s in chosen], dtype=float)
    smooth_centers = centers.copy()

    sections: list[Section] = []
    extrap_flags: list[bool] = []
    c1_flags: list[bool] = []
    for idx, (src, center) in enumerate(zip(chosen, smooth_centers)):
        outline = np.array(src.get("outline_3d") or [], dtype=float)
        if len(outline) < 8:
            u2 = np.array(src["outer_2d"], dtype=float)
            n, u, v = _normalise_frame(src["normal"], src["basis_u"], src["basis_v"])
            outline = np.asarray(center) + np.outer(u2[:, 0], u) + np.outer(u2[:, 1], v)
        if np.linalg.norm(outline[0] - outline[-1]) < 1e-9:
            outline = outline[:-1]
        outline = _smooth_loop(outline, 0 if is_repaired else smooth_passes)
        outline = outline + (center - np.asarray(src["position"], dtype=float))

        edge = _polygon_edge(outline)
        n, u, v = _normalise_frame(src["normal"], src["basis_u"], src["basis_v"])
        extrap_flags.append(str(src.get("measurement_status", "measured")) == "extrapolated")
        c1_flags.append(str(src.get("measurement_source", "")) == "c1_shape_extrapolation")
        sections.append(
            Section(
                index=idx,
                edge=edge,
                points=outline,
                center=center,
                normal=n,
                side=u,
                up=v,
                length=float(src.get("perimeter", 0.0)),
                axis_name="Z",
            )
        )

    prepare_sections(sections, half_samples)
    from .giant_ends import smooth_extrapolated_ends

    # Giant C1 stations already carry the measured shape gradient.  The old
    # C0 translation smoother would overwrite that derivative, so skip it
    # whenever a C1 station is present (legacy v1-only datasets still use it).
    has_c1 = any(
        c1_flags[i] for i, is_extrap in enumerate(extrap_flags) if is_extrap
    )
    if not has_c1:
        smooth_extrapolated_ends(sections, extrap_flags)

    # Keep only the outermost fabricated station at each end.  The
    # intermediate stations were also fabricated and over-constrain the loft;
    # one smooth continuation band is cleaner and avoids interpolation ripples
    # near the port.
    if any(extrap_flags) and not keep_all_extrapolated:
        last = len(sections) - 1
        sections = [
            sec for i, sec in enumerate(sections)
            if not extrap_flags[i] or i == 0 or i == last
        ]
    return sections


def build_giant_case(
    cache_path: str | Path,
    output_step: str | Path,
    step_mm: float = 2.0,
    half_samples: int = 65,
    smooth_passes: int = 2,
    keep_extrapolated: bool = False,
):
    cache_id = Path(cache_path).stem.replace("_measured", "")
    from .giant_pinned import pinned_giant_result

    pinned = pinned_giant_result(cache_id)
    if pinned is not None:
        write_step(pinned.solid, output_step)
        return pinned
    sections = load_giant_sections(
        cache_path, step_mm, half_samples, smooth_passes, keep_extrapolated
    )
    result = build_from_closed_sections(
        sections, BuildOptions(half_samples=half_samples, parting_axis="auto")
    )
    write_step(result.solid, output_step)
    return result
