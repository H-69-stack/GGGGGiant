"""Local monotone trend interpolation. Never overwrites raw_profile.

Suspect-interval isotonic snaps a short hill to a plateau-then-cliff.
This module fits dD/ds on the formline *outside* the hill and replaces the
interior with a monotone cubic that continues those slopes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .config import (
    MONOTONIC_NOISE_TOLERANCE_MM,
    SMOOTHING_MAX_EXPAND_MM,
    SMOOTHING_METHOD,
    SMOOTHING_TREND_WINDOW_MM,
)
from .isotonic import isotonic_decreasing
from .models import ProfileArrays
from .numeric import lt


def _context_span(
    s: np.ndarray,
    i_edge: int,
    *,
    side: str,
    window_mm: float,
) -> Optional[Tuple[int, int]]:
    n = int(s.size)
    if side == "left":
        j1 = i_edge - 1
        if j1 < 0:
            return None
        edge = float(s[i_edge])
        j0 = j1
        while j0 > 0 and edge - float(s[j0 - 1]) <= float(window_mm) + 1e-12:
            j0 -= 1
        return j0, j1
    j0 = i_edge + 1
    if j0 >= n:
        return None
    edge = float(s[i_edge])
    j1 = j0
    while j1 + 1 < n and float(s[j1 + 1]) - edge <= float(window_mm) + 1e-12:
        j1 += 1
    return j0, j1


def _fit_slope(s: np.ndarray, d: np.ndarray, span: Optional[Tuple[int, int]]) -> float:
    if span is None:
        return 0.0
    j0, j1 = span
    if j1 < j0:
        return 0.0
    ss = np.asarray(s[j0:j1 + 1], dtype=float)
    dd = np.asarray(d[j0:j1 + 1], dtype=float)
    if ss.size < 2 or abs(float(ss[-1] - ss[0])) < 1e-12:
        return 0.0
    m = float(np.polyfit(ss, dd, 1)[0])
    if not np.isfinite(m):
        return 0.0
    return min(m, 0.0)


def _fritsch_carlson_ok(
    y0: float,
    y1: float,
    x0: float,
    x1: float,
    m0: float,
    m1: float,
) -> bool:
    dx = float(x1 - x0)
    if dx <= 1e-12:
        return False
    dy = float(y1 - y0)
    if dy > MONOTONIC_NOISE_TOLERANCE_MM:
        return False
    alpha = dy / dx
    if abs(alpha) < 1e-15:
        return abs(m0) < 1e-9 and abs(m1) < 1e-9
    r0 = m0 / alpha
    r1 = m1 / alpha
    return r0 >= -1e-9 and r1 >= -1e-9 and r0 <= 3.0 + 1e-9 and r1 <= 3.0 + 1e-9


def _secant_matches_gentle_trend(
    y0: float,
    y1: float,
    x0: float,
    x1: float,
    m0: float,
    m1: float,
) -> bool:
    """True when the chord is at least as steep as the gentler outside slope.

    Fritsch–Carlson r<=3 only guarantees monotonicity. If both end slopes are
    steeper than the chord, a cubic Hermite goes flat in the middle.
    """
    if not _fritsch_carlson_ok(y0, y1, x0, x1, m0, m1):
        return False
    dx = float(x1 - x0)
    alpha = (float(y1) - float(y0)) / dx
    gentle = max(float(m0), float(m1))
    if gentle > -1e-6:
        return alpha <= 1e-9
    return alpha <= gentle + 1e-4


def _clamp_slopes(alpha: float, m0: float, m1: float) -> Tuple[float, float]:
    if abs(alpha) < 1e-15:
        return 0.0, 0.0
    if alpha < 0.0:
        lo, hi = 3.0 * alpha, 0.0
    else:
        lo, hi = 0.0, 3.0 * alpha
    return float(np.clip(m0, lo, hi)), float(np.clip(m1, lo, hi))


def _hermite(
    x: np.ndarray,
    x0: float,
    y0: float,
    m0: float,
    x1: float,
    y1: float,
    m1: float,
) -> np.ndarray:
    dx = float(x1 - x0)
    t = (np.asarray(x, dtype=float) - x0) / dx
    t2 = t * t
    t3 = t2 * t
    h00 = 2.0 * t3 - 3.0 * t2 + 1.0
    h10 = t3 - 2.0 * t2 + t
    h01 = -2.0 * t3 + 3.0 * t2
    h11 = t3 - t2
    return h00 * y0 + h10 * dx * m0 + h01 * y1 + h11 * dx * m1


def _expand_for_monotone(
    s: np.ndarray,
    d: np.ndarray,
    i0: int,
    i1: int,
    m0: float,
    m1: float,
    *,
    max_expand_mm: float,
) -> Tuple[int, int]:
    n = int(s.size)
    a, b = int(i0), int(i1)
    s_left_lim = float(s[i0]) - float(max_expand_mm)
    s_right_lim = float(s[i1]) + float(max_expand_mm)

    def _ok(aa: int, bb: int) -> bool:
        if aa <= 0 or bb + 1 >= n:
            return False
        return _secant_matches_gentle_trend(
            float(d[aa - 1]), float(d[bb + 1]),
            float(s[aa - 1]), float(s[bb + 1]),
            m0, m1,
        )

    if _ok(a, b):
        return a, b

    for _ in range(n):
        if _ok(a, b):
            return a, b
        if a <= 0 or b + 1 >= n:
            break
        x0, y0 = float(s[a - 1]), float(d[a - 1])
        x1, y1 = float(s[b + 1]), float(d[b + 1])
        alpha = (y1 - y0) / (x1 - x0) if abs(x1 - x0) > 1e-12 else 0.0
        grow_left = abs(alpha) < 1e-15 or (alpha != 0.0 and m0 / alpha > 3.0 + 1e-9)
        grow_right = abs(alpha) < 1e-15 or (alpha != 0.0 and m1 / alpha > 3.0 + 1e-9)
        grew = False
        if grow_right and b + 2 < n and float(s[b + 1]) <= s_right_lim + 1e-12:
            if not lt(float(d[a - 1]), float(d[b + 2]) - MONOTONIC_NOISE_TOLERANCE_MM):
                b += 1
                grew = True
        if not grew and grow_left and a - 1 > 0 and float(s[a - 1]) >= s_left_lim - 1e-12:
            if not lt(float(d[a - 2]), float(d[b + 1]) - MONOTONIC_NOISE_TOLERANCE_MM):
                a -= 1
                grew = True
        if not grew:
            if b + 2 < n and float(s[b + 1]) <= s_right_lim + 1e-12:
                if not lt(float(d[a - 1]), float(d[b + 2]) - MONOTONIC_NOISE_TOLERANCE_MM):
                    b += 1
                    grew = True
            if not grew and a - 1 > 0 and float(s[a - 1]) >= s_left_lim - 1e-12:
                if not lt(float(d[a - 2]), float(d[b + 1]) - MONOTONIC_NOISE_TOLERANCE_MM):
                    a -= 1
                    grew = True
        if not grew:
            break
    return a, b


def _smooth_segment(
    s: np.ndarray,
    d: np.ndarray,
    i0: int,
    i1: int,
) -> Tuple[int, int, np.ndarray]:
    n = int(d.size)
    left_span = _context_span(s, i0, side="left", window_mm=SMOOTHING_TREND_WINDOW_MM)
    right_span = _context_span(s, i1, side="right", window_mm=SMOOTHING_TREND_WINDOW_MM)
    m_left = _fit_slope(s, d, left_span)
    m_right = _fit_slope(s, d, right_span)

    a, b = _expand_for_monotone(
        s, d, i0, i1, m_left, m_right, max_expand_mm=SMOOTHING_MAX_EXPAND_MM,
    )
    left_i = a - 1 if a > 0 else None
    right_i = b + 1 if b + 1 < n else None
    xs = s[a:b + 1]

    if left_i is None and right_i is None:
        y = isotonic_decreasing(d[a:b + 1])
        return a, b, y

    if left_i is None:
        x1, y1 = float(s[right_i]), float(d[right_i])
        alpha = (y1 - float(d[a])) / (x1 - float(s[a])) if abs(x1 - float(s[a])) > 1e-12 else 0.0
        m0, m1 = _clamp_slopes(alpha, m_right, m_right)
        y = _hermite(xs, float(s[a]), float(d[a]), m0, x1, y1, m1)
        y[0] = float(d[a])
        y[-1] = max(float(y[-1]), y1) if alpha <= 0 else float(y[-1])
    elif right_i is None:
        x0, y0 = float(s[left_i]), float(d[left_i])
        alpha = (float(d[b]) - y0) / (float(s[b]) - x0) if abs(float(s[b]) - x0) > 1e-12 else 0.0
        m0, m1 = _clamp_slopes(alpha, m_left, m_left)
        y = _hermite(xs, x0, y0, m0, float(s[b]), float(d[b]), m1)
        y[-1] = float(d[b])
        y[0] = min(float(y[0]), y0)
    else:
        x0, y0 = float(s[left_i]), float(d[left_i])
        x1, y1 = float(s[right_i]), float(d[right_i])
        alpha = (y1 - y0) / (x1 - x0) if abs(x1 - x0) > 1e-12 else 0.0
        m0, m1 = m_left, m_right
        if not _secant_matches_gentle_trend(y0, y1, x0, x1, m0, m1):
            if not _fritsch_carlson_ok(y0, y1, x0, x1, m0, m1):
                m0, m1 = _clamp_slopes(alpha, m0, m1)
            else:
                m0, m1 = float(alpha), float(alpha)
        y = _hermite(xs, x0, y0, m0, x1, y1, m1)
        y[0] = min(float(y[0]), y0)
        y[-1] = max(float(y[-1]), y1)

    if left_i is not None:
        y[0] = min(float(y[0]), float(d[left_i]))
    if right_i is not None:
        y[-1] = max(float(y[-1]), float(d[right_i]))
    y = isotonic_decreasing(y)
    if left_i is not None:
        y[0] = min(float(y[0]), float(d[left_i]))
    if right_i is not None:
        y[-1] = max(float(y[-1]), float(d[right_i]))
    return a, b, y


def preview_local_smooth(
    profile: ProfileArrays,
    suspect_segments: Sequence[dict],
) -> Dict[str, Any]:
    """Return a candidate processed profile; original arrays are not mutated."""
    working = profile.copy()
    d = working.diameter
    s = working.s
    changed_ranges: List[dict] = []
    failed: List[dict] = []
    wrote_outside_suspect = False

    for seg in suspect_segments:
        i0 = int(seg["start_index"])
        i1 = int(seg["end_index"])
        i0 = max(0, min(i0, len(d) - 1))
        i1 = max(i0, min(i1, len(d) - 1))
        left = float(d[i0 - 1]) if i0 > 0 else None
        right = float(d[i1 + 1]) if i1 + 1 < len(d) else None
        if left is not None and right is not None and lt(left, right - MONOTONIC_NOISE_TOLERANCE_MM):
            failed.append({
                "start_index": i0,
                "end_index": i1,
                "reason": "anchors_not_non_increasing",
                "left": left,
                "right": right,
            })
            continue

        a, b, y_new = _smooth_segment(s, d, i0, i1)
        if a < i0 or b > i1:
            wrote_outside_suspect = True
        before = d[a:b + 1].copy()
        d[a:b + 1] = y_new
        changed_ranges.append({
            "start_index": a,
            "end_index": b,
            "start_s": float(s[a]),
            "end_s": float(s[b]),
            "before": before.tolist(),
            "after": np.asarray(y_new, dtype=float).tolist(),
        })

    working.diameter = d
    working.circumference = d * np.pi
    working.source = "stp_smoothed"
    ok = not failed
    return {
        "ok": ok,
        "method": SMOOTHING_METHOD,
        "profile": working,
        "changed_ranges": changed_ranges,
        "failed_segments": failed,
        "outside_unchanged": not wrote_outside_suspect,
    }


def apply_local_smooth(
    raw: ProfileArrays,
    working: ProfileArrays,
    suspect_segments: Sequence[dict],
) -> Dict[str, Any]:
    """Apply preview onto a copy of working. ``raw`` is never modified."""
    _ = raw  # explicit: raw is read-only by contract
    return preview_local_smooth(working, suspect_segments)
