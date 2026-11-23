"""Length validation and formline correction. Runs before smoothing.

raw_profile is never mutated. Callers store the returned copy as corrected_profile.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .config import (
    LENGTH_COMPARE_TOLERANCE_MM,
    LENGTH_EXTEND_TREND_WINDOW_MM,
    MIN_CENTERLINE_LENGTH_MM,
    MIN_DIAMETER_MM,
    MAX_DIAMETER_MM,
    SMALL_END_SIZE_DELTA_MM,
    SMALL_END_WINDOW_MM,
    STRAIGHT_MAX_SLOPE,
)
from .models import ProfileArrays
from .numeric import isclose, leq, lt
from .straight_segment_detector import detect_straight_segments


def _as_float_array(values) -> np.ndarray:
    return np.asarray(values, dtype=float)


def _sample_spacing(s: np.ndarray) -> float:
    if s.size < 2:
        return 1.0
    diffs = np.diff(s)
    diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
    if diffs.size == 0:
        return 1.0
    return float(np.median(diffs))


def _end_window_mask(s: np.ndarray, side: str, window_mm: float) -> np.ndarray:
    if s.size == 0:
        return np.zeros(0, dtype=bool)
    if side == "start":
        return s <= (float(s[0]) + float(window_mm) + 1e-12)
    return s >= (float(s[-1]) - float(window_mm) - 1e-12)


def _end_representative_diameter(s: np.ndarray, diameter: np.ndarray, side: str) -> float:
    mask = _end_window_mask(s, side, SMALL_END_WINDOW_MM)
    if not np.any(mask):
        return float(diameter[0] if side == "start" else diameter[-1])
    return float(np.median(diameter[mask]))


def identify_small_end(
    s: np.ndarray,
    diameter: np.ndarray,
    *,
    size_delta_mm: float = SMALL_END_SIZE_DELTA_MM,
) -> Dict[str, Any]:
    """Decide which axial end is the small-diameter end.

    Never defaults to array head / min-s. If both ends are within the size
    band, status is ``ambiguous`` and the caller must ask the engineer.
    """
    s = _as_float_array(s)
    d = _as_float_array(diameter)
    if s.size < 2 or d.size != s.size:
        return {
            "ok": False,
            "side": None,
            "status": "invalid",
            "d_start": None,
            "d_end": None,
            "delta": None,
            "message": "形线数据不足，无法判断小径端。",
        }
    d_start = _end_representative_diameter(s, d, "start")
    d_end = _end_representative_diameter(s, d, "end")
    delta = abs(d_start - d_end)
    if leq(delta, size_delta_mm, atol=0.0) or isclose(d_start, d_end, atol=size_delta_mm):
        return {
            "ok": False,
            "side": None,
            "status": "ambiguous",
            "d_start": d_start,
            "d_end": d_end,
            "delta": delta,
            "message": "两端截面尺寸接近，无法可靠判断小径端，请确认截短或补长方向。",
        }
    side = "start" if d_start < d_end else "end"
    return {
        "ok": True,
        "side": side,
        "status": "resolved",
        "d_start": d_start,
        "d_end": d_end,
        "delta": delta,
        "message": "",
    }


def resolve_correction_side(
    s: np.ndarray,
    diameter: np.ndarray,
    *,
    correction_side: Optional[str] = None,
) -> Dict[str, Any]:
    explicit = str(correction_side or "").strip().lower()
    if explicit in ("start", "end"):
        identified = identify_small_end(s, diameter)
        return {
            "ok": True,
            "side": explicit,
            "status": "engineer_confirmed",
            "d_start": identified.get("d_start"),
            "d_end": identified.get("d_end"),
            "delta": identified.get("delta"),
            "message": "",
        }
    return identify_small_end(s, diameter)


def _interp_series(s: np.ndarray, y: np.ndarray, s_new: np.ndarray) -> np.ndarray:
    if y is None or y.size != s.size:
        return np.full_like(s_new, np.nan, dtype=float)
    return np.interp(s_new, s, y)


def _build_keep_axis(s: np.ndarray, s_keep0: float, s_keep1: float) -> np.ndarray:
    spacing = _sample_spacing(s)
    inside = s[(s >= s_keep0 - 1e-12) & (s <= s_keep1 + 1e-12)]
    pts = [s_keep0]
    for v in inside:
        if abs(v - s_keep0) > 1e-9 and abs(v - s_keep1) > 1e-9:
            pts.append(float(v))
    if abs(s_keep1 - s_keep0) > 1e-12:
        pts.append(s_keep1)
    axis = np.unique(np.asarray(pts, dtype=float))
    if axis.size < 2:
        axis = np.array([s_keep0, s_keep1], dtype=float)
    # Guarantee the exact endpoints even if unique rounding collapsed them.
    axis[0] = s_keep0
    axis[-1] = s_keep1
    if axis.size == 1 or spacing <= 0:
        return axis
    return axis


def _resample_profile(
    profile: ProfileArrays,
    s_abs: np.ndarray,
    *,
    source: str,
    section_dims: Optional[Mapping[str, Any]] = None,
) -> Tuple[ProfileArrays, Optional[dict]]:
    s0 = _as_float_array(profile.s)
    c_new = _interp_series(s0, _as_float_array(profile.circumference), s_abs)
    d_new = _interp_series(s0, _as_float_array(profile.diameter), s_abs)
    new_profile = ProfileArrays(
        s=s_abs - float(s_abs[0]),
        circumference=c_new,
        diameter=d_new,
        source=source,
    )
    new_dims = None
    if section_dims:
        xs = _as_float_array(section_dims.get("s_mm") or [])
        new_dims = {"s_mm": new_profile.s.tolist()}
        for key in ("x_length_mm", "y_length_mm", "length_mm", "width_mm"):
            ys = _as_float_array(section_dims.get(key) or [])
            if xs.size and ys.size == xs.size:
                new_dims[key] = _interp_series(xs, ys, s_abs).tolist()
            else:
                new_dims[key] = []
    return new_profile, new_dims


def trim_profile_ends(
    profile: ProfileArrays,
    trim_start_mm: float,
    trim_end_mm: float,
    *,
    section_dims: Optional[Mapping[str, Any]] = None,
) -> Tuple[ProfileArrays, Optional[dict]]:
    """Delete length from one or both axial ends, then remap s so it starts at 0."""
    s = _as_float_array(profile.s)
    s0 = float(s[0])
    s1 = float(s[-1])
    trim_start = max(0.0, float(trim_start_mm))
    trim_end = max(0.0, float(trim_end_mm))
    keep0 = s0 + trim_start
    keep1 = s1 - trim_end
    if keep1 <= keep0 + 1e-12:
        raise ValueError("截短后有效长度必须大于 0")
    s_abs = _build_keep_axis(s, keep0, keep1)
    return _resample_profile(profile, s_abs, source="stp_length_corrected", section_dims=section_dims)


def trim_profile_from_small_end(
    profile: ProfileArrays,
    delta_mm: float,
    *,
    side: str,
    section_dims: Optional[Mapping[str, Any]] = None,
) -> Tuple[ProfileArrays, Optional[dict]]:
    """Delete ``delta_mm`` from the small-diameter end, then remap s to [0, L_drawing]."""
    delta = float(delta_mm)
    if side == "start":
        return trim_profile_ends(profile, delta, 0.0, section_dims=section_dims)
    return trim_profile_ends(profile, 0.0, delta, section_dims=section_dims)


def _local_slope(s: np.ndarray, d: np.ndarray, side: str, window_mm: float) -> Tuple[float, float, bool]:
    mask = _end_window_mask(s, side, window_mm)
    sw = s[mask]
    dw = d[mask]
    if sw.size < 2:
        anchor = float(d[0] if side == "start" else d[-1])
        return 0.0, anchor, True
    span = float(sw[-1] - sw[0])
    if span <= 1e-12:
        anchor = float(dw[0])
        return 0.0, anchor, True
    slope = float((dw[-1] - dw[0]) / span)
    anchor = float(dw[0] if side == "start" else dw[-1])
    straight = abs(slope) <= float(STRAIGHT_MAX_SLOPE)
    # Prefer the detector if this end already sits on a confirmed straight run.
    det = detect_straight_segments(s, d)
    for seg in det.get("segments") or []:
        if side == "start" and float(seg.get("start_s", 1e9)) <= float(s[0]) + 1.0:
            straight = True
            slope = 0.0
            break
        if side == "end" and float(seg.get("end_s", -1e9)) >= float(s[-1]) - 1.0:
            straight = True
            slope = 0.0
            break
    return slope, anchor, straight


def _extend_axis(s: np.ndarray, delta_mm: float, side: str) -> np.ndarray:
    spacing = max(0.25, _sample_spacing(s))
    extra = max(1, int(math_ceil(delta_mm / spacing)))
    if side == "end":
        start = float(s[-1])
        added = start + np.arange(1, extra + 1, dtype=float) * spacing
        added[-1] = start + float(delta_mm)
        return np.concatenate([s, added])
    start = float(s[0])
    added = start - np.arange(extra, 0, -1, dtype=float) * spacing
    added[0] = start - float(delta_mm)
    return np.concatenate([added, s])


def math_ceil(value: float) -> int:
    import math
    return int(math.ceil(value - 1e-12))


def _extrapolate_diameter(s_new: np.ndarray, s_old: np.ndarray, d_old: np.ndarray, side: str, slope: float, straight: bool) -> np.ndarray:
    d_new = np.interp(s_new, s_old, d_old)
    if side == "end":
        mask = s_new > float(s_old[-1]) + 1e-12
        origin_s = float(s_old[-1])
        origin_d = float(d_old[-1])
        use_slope = 0.0 if straight else slope
        d_new[mask] = origin_d + use_slope * (s_new[mask] - origin_s)
    else:
        mask = s_new < float(s_old[0]) - 1e-12
        origin_s = float(s_old[0])
        origin_d = float(d_old[0])
        use_slope = 0.0 if straight else slope
        d_new[mask] = origin_d + use_slope * (s_new[mask] - origin_s)
    d_new = np.clip(d_new, MIN_DIAMETER_MM, MAX_DIAMETER_MM)
    return d_new


def extend_profile_smoothly(
    profile: ProfileArrays,
    delta_mm: float,
    *,
    side: str,
    section_dims: Optional[Mapping[str, Any]] = None,
) -> Tuple[ProfileArrays, Optional[dict]]:
    """Extend missing length from the small-diameter end.

    Straight ends stay constant; taper ends use the local linear trend only
    (no high-order polynomial). The first new sample is continuous with the
    known endpoint.
    """
    s = _as_float_array(profile.s)
    d = _as_float_array(profile.diameter)
    slope, _anchor, straight = _local_slope(s, d, side, LENGTH_EXTEND_TREND_WINDOW_MM)
    s_abs = _extend_axis(s, float(delta_mm), side)
    d_new = _extrapolate_diameter(s_abs, s, d, side, slope, straight)
    c_new = d_new * np.pi
    new_profile = ProfileArrays(
        s=s_abs - float(s_abs[0]),
        circumference=c_new,
        diameter=d_new,
        source="stp_length_corrected",
    )
    new_dims = None
    if section_dims:
        xs = _as_float_array(section_dims.get("s_mm") or [])
        new_dims = {"s_mm": new_profile.s.tolist()}
        for key in ("x_length_mm", "y_length_mm", "length_mm", "width_mm"):
            ys = _as_float_array(section_dims.get(key) or [])
            if xs.size and ys.size == xs.size:
                y_abs = _interp_series(xs, ys, np.clip(s_abs, xs[0], xs[-1]))
                if side == "end":
                    extra = s_abs > float(s[-1]) + 1e-12
                    if np.any(extra) and ys.size:
                        y_abs[extra] = float(ys[-1])
                else:
                    extra = s_abs < float(s[0]) - 1e-12
                    if np.any(extra) and ys.size:
                        y_abs[extra] = float(ys[0])
                new_dims[key] = y_abs.tolist()
            else:
                new_dims[key] = []
    return new_profile, new_dims


def stp_length_of(profile: Optional[ProfileArrays]) -> float:
    if profile is None or profile.s is None or len(profile.s) == 0:
        return 0.0
    s = _as_float_array(profile.s)
    return float(s[-1] - s[0]) if s.size else 0.0


def allocate_end_corrections(
    difference_mm: float,
    small_end_correction_mm: Optional[float] = None,
    *,
    length_tolerance_mm: float = LENGTH_COMPARE_TOLERANCE_MM,
) -> Dict[str, Any]:
    """Split ``L_drawing - L_stp`` between the small and large ends.

    ``difference_mm`` is ``L_stp - L_drawing`` (positive = STP too long).
    Engineer correction is signed length *added* at that end:
    negative = trim, positive = extend.

    If ``small_end_correction_mm`` is omitted, the whole difference is
    assigned to the small end (legacy default).
    """
    needed = -float(difference_mm)
    if small_end_correction_mm is None:
        small = needed
        source = "default_small_end"
    else:
        try:
            small = float(small_end_correction_mm)
        except (TypeError, ValueError):
            return {
                "ok": False,
                "message": "小径端修正量必须是有效数字。",
            }
        if not np.isfinite(small):
            return {
                "ok": False,
                "message": "小径端修正量必须是有效数字。",
            }
        source = "engineer_small_end"
    large = needed - small
    if abs((small + large) - needed) > float(length_tolerance_mm) + 1e-12:
        return {
            "ok": False,
            "message": "小径端与大径端修正量之和必须等于图纸总长与 STP 长度之差。",
        }
    return {
        "ok": True,
        "needed_mm": needed,
        "small_end_correction_mm": small,
        "large_end_correction_mm": large,
        "source": source,
        "message": "",
    }


def apply_split_length_correction(
    profile: ProfileArrays,
    *,
    small_end_side: str,
    small_delta_mm: float,
    large_delta_mm: float,
    section_dims: Optional[Mapping[str, Any]] = None,
    length_tolerance_mm: float = LENGTH_COMPARE_TOLERANCE_MM,
) -> Tuple[ProfileArrays, Optional[dict]]:
    """Apply signed length changes at both ends. Positive = extend, negative = trim."""
    side = str(small_end_side)
    if side not in ("start", "end"):
        raise ValueError("小径端必须是 start 或 end。")
    start_delta = float(small_delta_mm) if side == "start" else float(large_delta_mm)
    end_delta = float(large_delta_mm) if side == "start" else float(small_delta_mm)
    trim_start = max(0.0, -start_delta)
    trim_end = max(0.0, -end_delta)
    ext_start = max(0.0, start_delta)
    ext_end = max(0.0, end_delta)
    L = stp_length_of(profile)
    if (trim_start + trim_end) >= max(L - float(MIN_CENTERLINE_LENGTH_MM), 0.0) - 1e-12:
        raise ValueError("截短量过大，修正后管件长度必须大于 0。")
    out = profile
    dims = section_dims
    tol = float(length_tolerance_mm)
    if trim_start > tol or trim_end > tol:
        out, dims = trim_profile_ends(out, trim_start, trim_end, section_dims=dims)
    if ext_start > tol:
        out, dims = extend_profile_smoothly(out, ext_start, side="start", section_dims=dims)
    if ext_end > tol:
        out, dims = extend_profile_smoothly(out, ext_end, side="end", section_dims=dims)
    return out, dims


def validate_profile_length(
    profile: ProfileArrays,
    drawing_length: float,
    *,
    section_dims: Optional[Mapping[str, Any]] = None,
    correction_side: Optional[str] = None,
    small_end_correction_mm: Optional[float] = None,
    length_tolerance_mm: float = LENGTH_COMPARE_TOLERANCE_MM,
) -> Dict[str, Any]:
    """Compare STP axial length with drawing length and correct if needed.

    Status:
        matched / trimmed / extended / ambiguous_end / error

    ``small_end_correction_mm`` is signed length added at the small-diameter
    end. Omit it to keep the legacy default (the whole difference is taken
    from the small end). The large-end amount is ``(L_drawing - L_stp) - small``.
    """
    L_drawing = float(drawing_length)
    L_stp = stp_length_of(profile)
    signed = L_stp - L_drawing
    abs_diff = abs(signed)
    base = {
        "drawing_length": round(L_drawing, 6),
        "stp_length": round(L_stp, 6),
        "difference": round(signed, 6),
        "correction_length": 0.0,
        "correction_side": None,
        "small_end": None,
        "small_end_correction_mm": 0.0,
        "large_end_correction_mm": 0.0,
        "correction_split_source": None,
        "message": "",
    }
    if L_drawing <= 0:
        return {
            "ok": False,
            "status": "error",
            "profile": None,
            "section_dims": None,
            "length_validation": {
                **base,
                "status": "error",
                "message": "图纸总长必须大于 0。",
            },
        }
    if leq(abs_diff, length_tolerance_mm, atol=0.0) or isclose(L_stp, L_drawing, atol=length_tolerance_mm):
        corrected = profile.copy()
        corrected.source = profile.source or "stp_raw"
        lv = {
            **base,
            "status": "matched",
            "message": "长度校验通过。",
        }
        return {
            "ok": True,
            "status": "matched",
            "profile": corrected,
            "section_dims": dict(section_dims) if section_dims else None,
            "length_validation": lv,
        }

    side_info = resolve_correction_side(profile.s, profile.diameter, correction_side=correction_side)
    if not side_info.get("ok"):
        lv = {
            **base,
            "status": "ambiguous_end",
            "small_end": side_info,
            "message": side_info.get("message") or "两端截面尺寸接近，无法可靠判断小径端，请确认截短或补长方向。",
        }
        return {
            "ok": False,
            "status": "ambiguous_end",
            "profile": None,
            "section_dims": None,
            "length_validation": lv,
        }

    alloc = allocate_end_corrections(
        signed,
        small_end_correction_mm,
        length_tolerance_mm=length_tolerance_mm,
    )
    if not alloc.get("ok"):
        lv = {
            **base,
            "status": "error",
            "small_end": side_info,
            "message": alloc.get("message") or "长度修正量不合法。",
        }
        return {
            "ok": False,
            "status": "error",
            "profile": None,
            "section_dims": None,
            "length_validation": lv,
        }

    side = side_info["side"]
    small_delta = float(alloc["small_end_correction_mm"])
    large_delta = float(alloc["large_end_correction_mm"])
    try:
        corrected, dims = apply_split_length_correction(
            profile,
            small_end_side=side,
            small_delta_mm=small_delta,
            large_delta_mm=large_delta,
            section_dims=section_dims,
            length_tolerance_mm=length_tolerance_mm,
        )
    except ValueError as exc:
        lv = {
            **base,
            "status": "error",
            "small_end": {"side": side, "d_start": side_info.get("d_start"), "d_end": side_info.get("d_end")},
            "small_end_correction_mm": round(small_delta, 6),
            "large_end_correction_mm": round(large_delta, 6),
            "message": str(exc),
        }
        return {
            "ok": False,
            "status": "error",
            "profile": None,
            "section_dims": None,
            "length_validation": lv,
        }

    status = "trimmed" if L_stp > L_drawing else "extended"
    if abs(large_delta) <= length_tolerance_mm:
        if status == "trimmed":
            msg = (
                f"STP 管件长度超过图纸总长 {abs_diff:.3f} mm。"
                "将以图纸总长为准，并从小径端开始删除差异长度。"
            )
        else:
            msg = (
                f"STP 管件长度不足图纸总长 {abs_diff:.3f} mm。"
                "将以图纸总长为准，差异长度由已知形线数据平滑获得。"
            )
    else:
        msg = (
            f"STP 与图纸长度差 {signed:.3f} mm。"
            f"小径端修正 {small_delta:.3f} mm，大径端修正 {large_delta:.3f} mm"
            "（正数补长，负数截短）。"
        )

    final_len = stp_length_of(corrected)
    lv = {
        **base,
        "status": status,
        "correction_length": round(abs_diff, 6),
        "correction_side": side,
        "small_end": {
            "side": side,
            "d_start": side_info.get("d_start"),
            "d_end": side_info.get("d_end"),
        },
        "small_end_correction_mm": round(small_delta, 6),
        "large_end_correction_mm": round(large_delta, 6),
        "correction_split_source": alloc.get("source"),
        "message": msg,
        "final_length": round(final_len, 6),
    }
    return {
        "ok": True,
        "status": status,
        "profile": corrected,
        "section_dims": dims,
        "length_validation": lv,
    }


def _section_arc(sec: Mapping[str, Any]) -> float:
    for key in ("cumulative_length", "中心线长度(mm)"):
        if key not in sec:
            continue
        try:
            return float(sec[key])
        except (TypeError, ValueError):
            continue
    return 0.0


def _section_position(sec: Mapping[str, Any]) -> np.ndarray:
    pos = sec.get("position")
    if isinstance(pos, (list, tuple)) and len(pos) >= 3:
        return np.asarray(pos[:3], float)
    return np.array([0.0, 0.0, _section_arc(sec)], float)


def _clone_section(sec: Mapping[str, Any], arc: float, position=None) -> dict:
    out = dict(sec)
    out["cumulative_length"] = round(float(arc), 6)
    if position is not None:
        out["position"] = [float(v) for v in np.asarray(position, float).reshape(-1)[:3]]
    return out


def _interp_section(a: Mapping[str, Any], b: Mapping[str, Any], arc: float) -> dict:
    sa, sb = _section_arc(a), _section_arc(b)
    t = 0.0 if abs(sb - sa) < 1e-12 else (float(arc) - sa) / (sb - sa)
    t = min(1.0, max(0.0, t))
    pos = _section_position(a) + t * (_section_position(b) - _section_position(a))
    src = a if t <= 0.5 else b
    return _clone_section(src, arc, pos)


def _sample_sections(sections: Sequence[Mapping[str, Any]], keep0: float, keep1: float) -> List[dict]:
    ss = np.array([_section_arc(s) for s in sections], float)
    targets = [keep0]
    for s in ss:
        if keep0 + 1e-9 < float(s) < keep1 - 1e-9:
            targets.append(float(s))
    targets.append(keep1)
    uniq = sorted({round(v, 6) for v in targets})
    out: List[dict] = []
    for t in uniq:
        if t <= float(ss[0]) + 1e-9:
            out.append(_clone_section(sections[0], t, _section_position(sections[0])))
        elif t >= float(ss[-1]) - 1e-9:
            out.append(_clone_section(sections[-1], t, _section_position(sections[-1])))
        else:
            i = int(np.searchsorted(ss, t) - 1)
            i = min(max(i, 0), len(sections) - 2)
            out.append(_interp_section(sections[i], sections[i + 1], t))
    return out


def _section_tangent(sections: Sequence[Mapping[str, Any]], side: str) -> np.ndarray:
    if side == "end":
        p0, p1 = _section_position(sections[-2]), _section_position(sections[-1])
    else:
        p0, p1 = _section_position(sections[0]), _section_position(sections[1])
    vec = p1 - p0
    norm = float(np.linalg.norm(vec))
    if norm < 1e-9:
        return np.array([0.0, 0.0, 1.0])
    return vec / norm


def _extend_sections(sections: List[dict], delta_mm: float, side: str) -> List[dict]:
    arcs = np.array([_section_arc(s) for s in sections], float)
    spacing = max(0.25, _sample_spacing(arcs))
    extra = max(1, math_ceil(float(delta_mm) / spacing))
    tangent = _section_tangent(sections, side)
    added: List[dict] = []
    if side == "end":
        start = float(arcs[-1])
        pos = _section_position(sections[-1])
        stations = start + np.arange(1, extra + 1, dtype=float) * spacing
        stations[-1] = start + float(delta_mm)
        for si in stations:
            rec = _clone_section(sections[-1], si, pos + tangent * (float(si) - start))
            rec["measurement_status"] = "extrapolated"
            added.append(rec)
        return list(sections) + added
    start = float(arcs[0])
    pos = _section_position(sections[0])
    stations = start - np.arange(extra, 0, -1, dtype=float) * spacing
    stations[0] = start - float(delta_mm)
    for si in stations:
        rec = _clone_section(sections[0], si, pos + tangent * (float(si) - start))
        rec["measurement_status"] = "extrapolated"
        added.append(rec)
    return added + list(sections)


def _rebase_section_arcs(sections: List[dict]) -> List[dict]:
    if not sections:
        return sections
    base = _section_arc(sections[0])
    out = []
    for sec in sections:
        rec = dict(sec)
        rec["cumulative_length"] = round(_section_arc(sec) - base, 6)
        out.append(rec)
    return out


def apply_split_length_correction_to_sections(
    sections: Sequence[Mapping[str, Any]],
    *,
    small_end_side: str,
    small_delta_mm: float,
    large_delta_mm: float,
    length_tolerance_mm: float = LENGTH_COMPARE_TOLERANCE_MM,
) -> List[dict]:
    """Apply the same signed small/large-end deltas to 3D section stations."""
    if not sections or len(sections) < 2:
        return [dict(s) for s in sections or []]
    side = str(small_end_side)
    if side not in ("start", "end"):
        return [dict(s) for s in sections]
    start_delta = float(small_delta_mm) if side == "start" else float(large_delta_mm)
    end_delta = float(large_delta_mm) if side == "start" else float(small_delta_mm)
    trim_start = max(0.0, -start_delta)
    trim_end = max(0.0, -end_delta)
    ext_start = max(0.0, start_delta)
    ext_end = max(0.0, end_delta)
    out = [dict(s) for s in sections]
    s0, s1 = _section_arc(out[0]), _section_arc(out[-1])
    if (trim_start + trim_end) >= max((s1 - s0) - float(MIN_CENTERLINE_LENGTH_MM), 0.0) - 1e-12:
        raise ValueError("截短量过大，修正后管件长度必须大于 0。")
    tol = float(length_tolerance_mm)
    if trim_start > tol or trim_end > tol:
        out = _sample_sections(out, s0 + trim_start, s1 - trim_end)
        out = _rebase_section_arcs(out)
    if ext_start > tol:
        out = _extend_sections(out, ext_start, "start")
        out = _rebase_section_arcs(out)
    if ext_end > tol:
        out = _extend_sections(out, ext_end, "end")
        out = _rebase_section_arcs(out)
    return out


def correction_args_from_validation(lv: Optional[Mapping[str, Any]]) -> Optional[dict]:
    if not isinstance(lv, dict) or lv.get("status") not in ("trimmed", "extended"):
        return None
    side = lv.get("correction_side") or (lv.get("small_end") or {}).get("side")
    if side not in ("start", "end"):
        return None
    try:
        small = float(lv.get("small_end_correction_mm") or 0)
        large = float(lv.get("large_end_correction_mm") or 0)
    except (TypeError, ValueError):
        return None
    return {
        "small_end_side": side,
        "small_delta_mm": small,
        "large_delta_mm": large,
    }
