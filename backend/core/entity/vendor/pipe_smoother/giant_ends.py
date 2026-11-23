"""Data-driven continuation of Giant's fabricated end stations.

Giant extrapolates the last measured centre line, but its end outlines are
simply translated copies of the last measured profile.  That makes the
*centre line* continuous while the outer contour stops following the measured
shape trend.  The helpers below replace only the extrapolated profiles:

* the measured profile nearest to the fabricated block is used as a shape
  template,
* its width / height are continued with a local linear trend of the adjacent
  measured sections,
* the target section keeps Giant's centre line and its own local frame.

The last extrapolated station is therefore tangential to the measured surface
at the joint instead of introducing a visible ridge.  No fillet is involved.
"""
from __future__ import annotations

import numpy as np

from .geometry import Section


def _local(sec: Section, points: np.ndarray) -> np.ndarray:
    d = np.asarray(points, dtype=float) - sec.center
    return np.column_stack([d @ sec.side, d @ sec.up])


def _extent(local: np.ndarray) -> tuple[float, float]:
    lo = local.min(axis=0)
    hi = local.max(axis=0)
    return max(float(hi[0] - lo[0]), 1e-9), max(float(hi[1] - lo[1]), 1e-9)


def _section_extent(sec: Section) -> tuple[float, float]:
    return _extent(np.vstack([_local(sec, sec.top_pts), _local(sec, sec.bottom_pts)]))


def _predict_by_point(
    anchor: Section,
    good_sections: list[Section],
    t_good: np.ndarray,
    t_target: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Continue every outline point along its own measured trajectory.

    Scaling the whole anchor profile by a width/height ratio is only correct
    when the profile remains geometrically similar.  The real profile changes
    differently around its circumference, so the scaled copy can leave the
    measured surface with a local bulge even when its overall width is right.
    Here every point index is fitted against the adjacent measured sections
    and evaluated at the fabricated station.  All points therefore share the
    same local axial trend as the measured surface they continue.
    """
    n_top = len(anchor.top_pts)
    n_bottom = len(anchor.bottom_pts)
    if n_top < 3 or n_bottom < 3:
        raise ValueError("empty section outline")
    if any(len(s.top_pts) != n_top or len(s.bottom_pts) != n_bottom for s in good_sections):
        raise ValueError("section point counts differ")

    def predict(src: np.ndarray) -> np.ndarray:
        x = np.asarray(t_good, dtype=float)
        a = np.vstack([x, np.ones_like(x)]).T
        out = np.empty_like(src, dtype=float)
        for j in range(len(src)):
            values = np.array([s.top_pts[j] if src is anchor.top_pts else s.bottom_pts[j] for s in good_sections])
            coeff, *_ = np.linalg.lstsq(a, values, rcond=None)
            out[j] = coeff[0] * t_target + coeff[1]
        return out

    return predict(anchor.top_pts), predict(anchor.bottom_pts)


def _shape_block(bad: list[int], good: list[int], sections: list[Section]) -> None:
    """Continue the nearest measured profile through the fabricated stations."""
    if not bad or len(good) < 4:
        return

    anchor = sections[good[0]] if bad[0] < good[0] else sections[good[-1]]
    good_sections = [sections[i] for i in good]

    # Axial direction points from the measured block into the fabricated block.
    if bad[0] < good[0]:
        direction = sections[bad[0]].center - anchor.center
    else:
        direction = sections[bad[-1]].center - anchor.center
    direction /= max(np.linalg.norm(direction), 1e-12)

    t_good = np.array([float((s.center - anchor.center) @ direction) for s in good_sections])

    for target in bad:
        sec = sections[target]
        t = float((sec.center - anchor.center) @ direction)

        # Keep the measured outline itself and carry it along the measured
        # centre line.  Scaling the whole profile distorts the part of the
        # circumference whose local width trend differs from the global one
        # and leaves a small S-bulge at the joint.  Translation preserves the
        # exact local curvature of the measured section, so the surface leaves
        # the last measured station without a kink.
        top_l = _local(anchor, anchor.top_pts)
        bottom_l = _local(anchor, anchor.bottom_pts)
        w_anchor, h_anchor = _section_extent(anchor)
        widths = np.array([_section_extent(s)[0] for s in good_sections])
        heights = np.array([_section_extent(s)[1] for s in good_sections])
        # Allow only a very small trend correction; large fashion scaling is
        # what generated the visible bump.
        sw = float(np.clip(np.polyval(np.polyfit(t_good, widths, 1), t) / w_anchor, 0.98, 1.02))
        sh = float(np.clip(np.polyval(np.polyfit(t_good, heights, 1), t) / h_anchor, 0.98, 1.02))
        top_l = top_l * np.array([sw, sh])
        bottom_l = bottom_l * np.array([sw, sh])
        new_top = sec.center + top_l[:, :1] * sec.side + top_l[:, 1:] * sec.up
        new_bottom = sec.center + bottom_l[:, :1] * sec.side + bottom_l[:, 1:] * sec.up

        # Keep the station in the measured centre-line plane and preserve the
        # station centre used by the global surface interpolation.
        for arr in (new_top, new_bottom):
            arr -= ((arr - sec.center) @ sec.normal)[:, None] * sec.normal

        # Make the two half-seams share one point on each side.
        new_bottom[-1] = new_top[0]
        new_bottom[0] = new_top[-1]

        tau_r = (new_top[1] - new_top[0]) + (new_bottom[-1] - new_bottom[-2])
        tau_l = (new_top[-1] - new_top[-2]) + (new_bottom[1] - new_bottom[0])
        tau_r /= max(np.linalg.norm(tau_r), 1e-12)
        tau_l /= max(np.linalg.norm(tau_l), 1e-12)

        sec.top_pts = new_top
        sec.bottom_pts = new_bottom
        sec.points = np.vstack([new_top, new_bottom[1:-1]])
        sec.right = new_top[0]
        sec.left = new_top[-1]
        sec.tangent_right = tau_r
        sec.tangent_left = tau_l
        sec.length = float(np.sum(np.linalg.norm(np.diff(np.vstack([sec.points, sec.points[:1]]), axis=0), axis=1)))


def smooth_extrapolated_ends(sections: list[Section], flags: list[bool]) -> None:
    """Replace fabricated end outlines with a continuation of measured shape."""
    n = len(sections)
    if n < 8:
        return

    first_measured = next((i for i, f in enumerate(flags) if not f), None)
    if first_measured is not None and first_measured > 0:
        bad = list(range(0, first_measured))
        good = list(range(first_measured, min(n, first_measured + 8)))
        _shape_block(bad, good, sections)

    last_measured = max((i for i, f in enumerate(flags) if not f), default=-1)
    if last_measured >= 0 and last_measured + 1 < n:
        bad = list(range(last_measured + 1, n))
        good = list(range(max(0, last_measured - 7), last_measured + 1))
        _shape_block(bad, good, sections)
