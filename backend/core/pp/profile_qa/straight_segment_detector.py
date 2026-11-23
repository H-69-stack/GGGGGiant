"""Straight-tube candidate detector.

A region is a candidate straight only if OD stays within ±tolerance *and*
the overall slope is near zero. Slow tapers with tiny step-to-step changes
are rejected by the slope / cumulative-drop checks.

Local 40 mm windows are intentionally strict so a slow taper is not called
straight. After those windows, nearby same-diameter runs are merged and
grown using the *global* ±tol test, then trimmed with a short inward-only
window so a residual 0.1 mm shelf does not split one plateau, while the
large-end taper tail is still cut away.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from .config import (
    ALGORITHM_VERSION,
    MIN_STRAIGHT_LENGTH_MM,
    MIN_STRAIGHT_POINTS,
    QA_QUANTITY,
    STRAIGHT_GROW_WINDOW_MM,
    STRAIGHT_MAX_SLOPE,
    STRAIGHT_TOLERANCE_MM,
    STRAIGHT_WINDOW_MM,
)
from .numeric import gt, threshold_leq

IndexSpan = Tuple[int, int]


def _window_indices(s: np.ndarray, center: int, window_mm: float) -> slice:
    half = float(window_mm) * 0.5
    s0 = float(s[center]) - half
    s1 = float(s[center]) + half
    i0 = int(np.searchsorted(s, s0, side="left"))
    i1 = int(np.searchsorted(s, s1, side="right")) - 1
    i0 = max(0, i0)
    i1 = max(i0, min(i1, len(s) - 1))
    return slice(i0, i1 + 1)


def _linreg_slope(s: np.ndarray, d: np.ndarray) -> float:
    if s.size < 2:
        return 0.0
    x = s - np.mean(s)
    y = d - np.mean(d)
    den = float(np.dot(x, x))
    if den <= 1e-18:
        return 0.0
    return float(np.dot(x, y) / den)


def _span_metrics(s: np.ndarray, d: np.ndarray, i0: int, i1: int) -> Dict[str, float]:
    seg_s = s[i0:i1 + 1]
    seg_d = d[i0:i1 + 1]
    representative = float(np.median(seg_d))
    return {
        "length": float(seg_s[-1] - seg_s[0]),
        "rng": float(np.max(seg_d) - np.min(seg_d)),
        "slope": _linreg_slope(seg_s, seg_d),
        "representative": representative,
        "max_dev": float(np.max(np.abs(seg_d - representative))),
        "cum_drop": abs(float(seg_d[-1] - seg_d[0])),
        "npts": float(i1 - i0 + 1),
    }


def _span_is_straight(
    s: np.ndarray,
    d: np.ndarray,
    i0: int,
    i1: int,
    *,
    half_range: float,
    max_slope: float,
    min_points: int,
    min_length_mm: float,
) -> bool:
    if i0 < 0 or i1 >= len(s) or i1 < i0:
        return False
    npts = int(i1 - i0 + 1)
    if npts < min_points:
        return False
    m = _span_metrics(s, d, i0, i1)
    if gt(min_length_mm, m["length"]):
        return False
    return (
        threshold_leq(m["rng"], 2.0 * half_range)
        and threshold_leq(abs(m["slope"]), max_slope)
        and threshold_leq(m["max_dev"], half_range)
        and threshold_leq(m["cum_drop"], 2.0 * half_range)
    )


def _inward_window_flat(
    s: np.ndarray,
    d: np.ndarray,
    edge: int,
    other: int,
    *,
    window_mm: float,
    half_range: float,
    max_slope: float,
) -> bool:
    """True if the short window from ``edge`` toward ``other`` looks locally flat."""
    if edge == other:
        return True
    direction = 1 if other > edge else -1
    target = float(s[edge]) + direction * float(window_mm)
    if direction > 0:
        j = int(np.searchsorted(s, target, side="right")) - 1
        j = min(max(j, edge), other)
        sl = slice(edge, j + 1)
    else:
        j = int(np.searchsorted(s, target, side="left"))
        j = max(min(j, edge), other)
        sl = slice(j, edge + 1)
    sw = s[sl]
    dw = d[sl]
    if dw.size < 3:
        return True
    rng = float(np.max(dw) - np.min(dw))
    slope = abs(_linreg_slope(sw, dw))
    return threshold_leq(rng, 2.0 * half_range) and threshold_leq(slope, max_slope)


def _coalesce_spans(
    spans: Sequence[IndexSpan],
    s: np.ndarray,
    d: np.ndarray,
    *,
    half_range: float,
    max_slope: float,
    min_points: int,
    min_length_mm: float,
) -> List[List[int]]:
    if not spans:
        return []
    ordered = sorted((int(a), int(b)) for a, b in spans)
    out: List[List[int]] = [[ordered[0][0], ordered[0][1]]]
    for i0, i1 in ordered[1:]:
        p0, p1 = out[-1]
        joined1 = max(p1, i1)
        if _span_is_straight(
            s, d, p0, joined1,
            half_range=half_range, max_slope=max_slope,
            min_points=min_points, min_length_mm=min_length_mm,
        ):
            out[-1][1] = joined1
            out[-1][0] = min(p0, i0)
        else:
            out.append([i0, i1])
    return out


def _grow_span(
    s: np.ndarray,
    d: np.ndarray,
    i0: int,
    i1: int,
    *,
    half_range: float,
    max_slope: float,
    min_points: int,
    min_length_mm: float,
) -> IndexSpan:
    n = int(s.size)
    while i0 > 0 and _span_is_straight(
        s, d, i0 - 1, i1,
        half_range=half_range, max_slope=max_slope,
        min_points=min_points, min_length_mm=min_length_mm,
    ):
        i0 -= 1
    while i1 + 1 < n and _span_is_straight(
        s, d, i0, i1 + 1,
        half_range=half_range, max_slope=max_slope,
        min_points=min_points, min_length_mm=min_length_mm,
    ):
        i1 += 1
    return i0, i1


def _trim_span(
    s: np.ndarray,
    d: np.ndarray,
    i0: int,
    i1: int,
    *,
    grow_window_mm: float,
    half_range: float,
    max_slope: float,
    min_points: int,
    min_length_mm: float,
) -> IndexSpan:
    """Pull edges back until the inward window is flat, keeping min length."""
    while i0 < i1 and not _inward_window_flat(
        s, d, i0, i1,
        window_mm=grow_window_mm, half_range=half_range, max_slope=max_slope,
    ):
        if not _span_is_straight(
            s, d, i0 + 1, i1,
            half_range=half_range, max_slope=max_slope,
            min_points=min_points, min_length_mm=min_length_mm,
        ):
            break
        i0 += 1
    while i1 > i0 and not _inward_window_flat(
        s, d, i1, i0,
        window_mm=grow_window_mm, half_range=half_range, max_slope=max_slope,
    ):
        if not _span_is_straight(
            s, d, i0, i1 - 1,
            half_range=half_range, max_slope=max_slope,
            min_points=min_points, min_length_mm=min_length_mm,
        ):
            break
        i1 -= 1
    return i0, i1


def _emit_segment(s: np.ndarray, d: np.ndarray, i0: int, i1: int, sid: int) -> dict:
    m = _span_metrics(s, d, i0, i1)
    representative = m["representative"]
    return {
        "segment_id": f"S{sid}",
        "start_s": float(s[i0]),
        "end_s": float(s[i1]),
        "length": m["length"],
        "representative_diameter": round(representative, 3),
        "representative_circumference": round(representative * np.pi, 3),
        "max_deviation": m["max_dev"],
        "slope": m["slope"],
        "status": "candidate",
        "start_index": int(i0),
        "end_index": int(i1),
        "point_count": int(i1 - i0 + 1),
        "algorithm_version": ALGORITHM_VERSION,
        "confirmed_by": None,
        "confirmed_at": None,
    }


def detect_straight_segments(
    s: np.ndarray,
    diameter: np.ndarray,
    *,
    tolerance_mm: float = STRAIGHT_TOLERANCE_MM,
    max_slope: float = STRAIGHT_MAX_SLOPE,
    min_length_mm: float = MIN_STRAIGHT_LENGTH_MM,
    window_mm: float = STRAIGHT_WINDOW_MM,
    min_points: int = MIN_STRAIGHT_POINTS,
    grow_window_mm: float = STRAIGHT_GROW_WINDOW_MM,
) -> Dict[str, Any]:
    s = np.asarray(s, dtype=float)
    d = np.asarray(diameter, dtype=float)
    n = int(s.size)
    if n < min_points:
        return {"segments": [], "metrics": {"quantity": QA_QUANTITY, "n": n}}

    half_range = float(tolerance_mm)  # ±tol → max |D - median| and (max-min)/2
    is_flat = np.zeros(n, dtype=bool)
    for i in range(n):
        sl = _window_indices(s, i, window_mm)
        dw = d[sl]
        sw = s[sl]
        if dw.size < 3:
            continue
        rng = float(np.max(dw) - np.min(dw))
        slope = abs(_linreg_slope(sw, dw))
        # range within ±tol around a representative value ⇒ max-min <= 2*tol
        if threshold_leq(rng, 2.0 * half_range) and threshold_leq(slope, max_slope):
            is_flat[i] = True

    raw_segs: List[IndexSpan] = []
    i = 0
    while i < n:
        if not is_flat[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and is_flat[j + 1]:
            j += 1
        raw_segs.append((i, j))
        i = j + 1

    accepted: List[IndexSpan] = []
    for i0, i1 in raw_segs:
        if _span_is_straight(
            s, d, i0, i1,
            half_range=half_range, max_slope=max_slope,
            min_points=min_points, min_length_mm=min_length_mm,
        ):
            accepted.append((i0, i1))

    merged = _coalesce_spans(
        accepted, s, d,
        half_range=half_range, max_slope=max_slope,
        min_points=min_points, min_length_mm=min_length_mm,
    )
    grown: List[IndexSpan] = []
    for i0, i1 in merged:
        g0, g1 = _grow_span(
            s, d, i0, i1,
            half_range=half_range, max_slope=max_slope,
            min_points=min_points, min_length_mm=min_length_mm,
        )
        t0, t1 = _trim_span(
            s, d, g0, g1,
            grow_window_mm=grow_window_mm, half_range=half_range,
            max_slope=max_slope, min_points=min_points, min_length_mm=min_length_mm,
        )
        grown.append((t0, t1))

    final_spans = _coalesce_spans(
        grown, s, d,
        half_range=half_range, max_slope=max_slope,
        min_points=min_points, min_length_mm=min_length_mm,
    )

    out: List[dict] = []
    sid = 0
    for i0, i1 in final_spans:
        if not _span_is_straight(
            s, d, i0, i1,
            half_range=half_range, max_slope=max_slope,
            min_points=min_points, min_length_mm=min_length_mm,
        ):
            continue
        out.append(_emit_segment(s, d, i0, i1, sid))
        sid += 1

    return {
        "segments": out,
        "metrics": {
            "quantity": QA_QUANTITY,
            "n": n,
            "tolerance_mm": half_range,
            "max_slope": max_slope,
            "min_length_mm": min_length_mm,
            "grow_window_mm": float(grow_window_mm),
            "candidate_count": len(out),
            "algorithm_version": ALGORITHM_VERSION,
        },
    }
