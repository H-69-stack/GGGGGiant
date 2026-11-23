"""Monotonic formline validator: robust trend + residual anomalies.

QA decisions use outer diameter (mm), derived from stored circumference via D=C/π,
then rounded to 2 decimal places for identification. Raw stored profiles are not
overwritten. Isotonic regression is a *reference only*.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .config import (
    ALGORITHM_VERSION,
    ANOMALY_MERGE_GAP_POINTS,
    ANOMALY_RESIDUAL_ABS_MIN_MM,
    ANOMALY_RESIDUAL_K,
    BUMP_BASELINE_WINDOW_POINTS,
    BUMP_MIN_LENGTH_MM,
    BUMP_PEAK_PROMINENCE_MM,
    BUMP_RETURN_EPS_MM,
    DIAMETER_DECIMALS,
    LOCAL_RISE_MIN_LENGTH_MM,
    LOCAL_RISE_MIN_MM,
    LOCAL_RISE_MIN_POINTS,
    LOCAL_RISE_STEP_MIN_MM,
    MAX_LOCAL_ANOMALY_LENGTH_RATIO,
    MAX_LOCAL_ANOMALY_POINT_RATIO,
    MAX_LOCAL_ANOMALY_SEGMENTS,
    MIN_ANOMALY_SEGMENT_LENGTH_MM,
    MONOTONIC_NOISE_TOLERANCE_MM,
    OSCILLATION_SIGN_MIN_MM,
    QA_QUANTITY,
    ROLLING_MEDIAN_WINDOW,
    SEVERITY_HIGH_MM,
    SEVERITY_MEDIUM_MM,
    TREND_FIT_QUALITY_MIN,
    TREND_MAD_MAX_MM,
    TREND_SCATTER_RATIO_MAX,
)
from .isotonic import isotonic_decreasing
from .models import SuspectSegment
from .numeric import gt, ratio_leq, threshold_leq
from .state import QA_LOCAL_ANOMALY, QA_MANUAL_REQUIRED, QA_MONOTONIC_PASS


def quantize_diameter(diameter: np.ndarray, decimals: int = DIAMETER_DECIMALS) -> np.ndarray:
    """Keep identification OD at millimetre hundredths (0.01 mm)."""
    return np.round(np.asarray(diameter, dtype=float), int(decimals))


def _decreasing_reference(s: np.ndarray, d_det: np.ndarray) -> np.ndarray:
    """Decreasing isotonic on the interior, linearly extrapolated to the ends.

    Full-series isotonic hides start spikes because they become the envelope.
    Fitting the core and extrapolating with the local core slope keeps a
    physically decreasing reference at the ends.
    """
    n = int(d_det.size)
    if n < 8:
        return isotonic_decreasing(d_det)
    trim = max(8, int(0.10 * n))
    if n - 2 * trim < 8:
        trim = max(2, n // 10)
    if n - 2 * trim < 8:
        return isotonic_decreasing(d_det)
    s_core = s[trim:n - trim]
    d_core_ref = isotonic_decreasing(d_det[trim:n - trim])
    ref = np.interp(s, s_core, d_core_ref)
    k = max(1, min(20, s_core.size // 4))
    ds_l = float(s_core[k] - s_core[0])
    slope_l = float(d_core_ref[k] - d_core_ref[0]) / ds_l if abs(ds_l) > 1e-12 else 0.0
    ds_r = float(s_core[-1] - s_core[-1 - k])
    slope_r = float(d_core_ref[-1] - d_core_ref[-1 - k]) / ds_r if abs(ds_r) > 1e-12 else 0.0
    left = s < s_core[0]
    right = s > s_core[-1]
    ref[left] = d_core_ref[0] + slope_l * (s[left] - s_core[0])
    ref[right] = d_core_ref[-1] + slope_r * (s[right] - s_core[-1])
    return ref


def rolling_median(y: np.ndarray, window: int) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    n = int(y.size)
    if n == 0 or window <= 1:
        return y.copy()
    w = int(window) if int(window) % 2 == 1 else int(window) + 1
    pad = w // 2
    yp = np.pad(y, (pad, pad), mode="edge")
    out = np.empty(n, dtype=float)
    for i in range(n):
        out[i] = float(np.median(yp[i:i + w]))
    return out


def _mad(residual: np.ndarray) -> float:
    r = residual[np.isfinite(residual)]
    if r.size == 0:
        return 0.0
    med = float(np.median(r))
    return float(1.4826 * np.median(np.abs(r - med)))


def _merge_runs(mask: np.ndarray, s: np.ndarray, residual: np.ndarray) -> List[SuspectSegment]:
    n = int(mask.size)
    if n == 0:
        return []
    # Fill small gaps so a 2-point hole does not split one physical bump.
    filled = mask.copy()
    gap = int(ANOMALY_MERGE_GAP_POINTS)
    last_true = -10**9
    for i, flag in enumerate(filled):
        if flag:
            if 0 < i - last_true - 1 <= gap and last_true >= 0:
                filled[last_true + 1:i] = True
            last_true = i

    segments: List[SuspectSegment] = []
    i = 0
    while i < n:
        if not filled[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and filled[j + 1]:
            j += 1
        res = residual[i:j + 1]
        length = float(s[j] - s[i])
        if j == i:
            if i > 0:
                length = max(length, float(s[i] - s[i - 1]))
            if i + 1 < n:
                length = max(length, float(s[i + 1] - s[i]))
        max_dev = float(np.max(res)) if res.size else 0.0
        mean_res = float(np.mean(res)) if res.size else 0.0
        if max_dev >= SEVERITY_HIGH_MM:
            severity = "high"
        elif max_dev >= SEVERITY_MEDIUM_MM:
            severity = "medium"
        else:
            severity = "low"
        segments.append(
            SuspectSegment(
                start_s=float(s[i]),
                end_s=float(s[j]),
                start_index=int(i),
                end_index=int(j),
                reason="local_monotonicity_violation",
                severity=severity,
                max_positive_deviation=max_dev,
                mean_residual=mean_res,
                point_count=int(j - i + 1),
                length=float(length),
            )
        )
        i = j + 1
    return segments


def _segment_stats(s: np.ndarray, residual: np.ndarray, i0: int, i1: int) -> SuspectSegment:
    res = residual[i0:i1 + 1]
    length = float(s[i1] - s[i0]) if i1 > i0 else 0.0
    if i1 == i0:
        if i0 > 0:
            length = max(length, float(s[i0] - s[i0 - 1]))
        if i0 + 1 < s.size:
            length = max(length, float(s[i0 + 1] - s[i0]))
    max_dev = float(np.max(res)) if res.size else 0.0
    mean_res = float(np.mean(res)) if res.size else 0.0
    if max_dev >= SEVERITY_HIGH_MM:
        severity = "high"
    elif max_dev >= SEVERITY_MEDIUM_MM:
        severity = "medium"
    else:
        severity = "low"
    return SuspectSegment(
        start_s=float(s[i0]),
        end_s=float(s[i1]),
        start_index=int(i0),
        end_index=int(i1),
        reason="local_monotonicity_violation",
        severity=severity,
        max_positive_deviation=max_dev,
        mean_residual=mean_res,
        point_count=int(i1 - i0 + 1),
        length=float(length),
    )


def _is_diameter_bump(
    diameter: np.ndarray,
    i0: int,
    i1: int,
    prominence: float,
) -> tuple[bool, int]:
    """True when the residual run is a local OD hill, or a start/end step.

    A smooth taper that only lags the isotonic envelope has a fall but no rise
    in the middle of the tube — those runs are not geometric bumps.
    """
    n = int(diameter.size)
    sl = diameter[i0:i1 + 1]
    if sl.size == 0:
        return False, i0
    peak = int(i0 + int(np.argmax(sl)))
    left = float(diameter[max(0, i0 - 1)])
    right = float(diameter[min(n - 1, i1 + 1)])
    peak_val = float(diameter[peak])
    step = float(prominence)
    has_rise = (peak_val - left) >= step
    has_fall = (peak_val - right) >= step
    if has_rise and has_fall:
        return True, peak
    # A leading monotone taper (fall only) is not a bulge.
    if i1 >= n - 1 and has_rise:
        return True, peak
    return False, peak


def _expand_to_baseline(
    diameter: np.ndarray,
    peak: int,
    i0: int,
    i1: int,
    *,
    return_eps: float,
    window: int,
) -> tuple[int, int]:
    n = int(diameter.size)
    a = int(peak)
    b = int(peak)
    i = int(peak)
    # Sudden-rise start = first point higher than its predecessor on this hill.
    while i > 0 and float(diameter[i]) > float(diameter[i - 1]) + 1e-12:
        a = i
        i -= 1
    while a < peak - 4 and not np.any(diameter[peak:] < diameter[a]):
        a += 1
    start_d = float(diameter[a])
    # End = first later point with D < start D, not the first drop from the peak.
    while b + 1 < n and float(diameter[b + 1]) >= start_d:
        b += 1
    if b + 1 < n and float(diameter[b + 1]) < start_d:
        b += 1
    return a, b


def _merge_overlapping(segments: List[SuspectSegment], s: np.ndarray, residual: np.ndarray) -> List[SuspectSegment]:
    if not segments:
        return []
    ordered = sorted(segments, key=lambda seg: (seg.start_index, seg.end_index))
    merged: List[SuspectSegment] = [ordered[0]]
    for seg in ordered[1:]:
        prev = merged[-1]
        if seg.start_index <= prev.end_index + 1:
            merged[-1] = _segment_stats(s, residual, prev.start_index, max(prev.end_index, seg.end_index))
        else:
            merged.append(seg)
    return merged


def expand_local_bumps(
    segments: Sequence[SuspectSegment],
    s: np.ndarray,
    diameter: np.ndarray,
    residual: np.ndarray,
    *,
    prominence: float = BUMP_PEAK_PROMINENCE_MM,
    return_eps: float = BUMP_RETURN_EPS_MM,
    window: int = BUMP_BASELINE_WINDOW_POINTS,
    min_length: float = BUMP_MIN_LENGTH_MM,
) -> List[SuspectSegment]:
    """Keep only true OD bumps and expand each to the local baseline.

    Residual thresholding only marks the peak. Smoothing / UI need the full
    geometric feature (rise from plateau through return to plateau).
    Taper-lag runs without a local peak are dropped.
    """
    expanded: List[SuspectSegment] = []
    for seg in segments:
        ok, peak = _is_diameter_bump(diameter, seg.start_index, seg.end_index, prominence)
        if not ok:
            continue
        i0, i1 = _expand_to_baseline(
            diameter, peak, seg.start_index, seg.end_index,
            return_eps=return_eps, window=window,
        )
        out = _segment_stats(s, residual, i0, i1)
        if out.length + 1e-9 < float(min_length) and out.point_count < 8:
            continue
        expanded.append(out)
    return _merge_overlapping(expanded, s, residual)


def detect_local_rise_segments(
    s: np.ndarray,
    diameter: np.ndarray,
    residual: np.ndarray,
    *,
    min_rise: float = LOCAL_RISE_MIN_MM,
    min_length: float = LOCAL_RISE_MIN_LENGTH_MM,
    min_up_points: int = LOCAL_RISE_MIN_POINTS,
) -> List[SuspectSegment]:
    """Detect geometric OD bulges independently of isotonic residual.

    Start = the point where OD suddenly rises. If the following uptrend
    lasts more than ``min_up_points`` samples, it is a bulge. The end is
    the first later point whose OD is *below the start OD*, not the first
    point that is only below its predecessor (that is still the downhill).
    A long plateau after a tiny step is not an uptrend; only a prominent
    jump that stays at the new level is treated as a shelf bulge.
    """
    s = np.asarray(s, dtype=float)
    d = quantize_diameter(diameter)
    n = int(d.size)
    if n < 3:
        return []
    segments: List[SuspectSegment] = []
    i = 1
    min_up = int(min_up_points)
    step_min = float(LOCAL_RISE_STEP_MIN_MM)
    prominence = float(BUMP_PEAK_PROMINENCE_MM)
    shelf_eps = float(BUMP_RETURN_EPS_MM)
    while i < n:
        if not (float(d[i]) > float(d[i - 1]) + step_min):
            i += 1
            continue
        start = i  # sudden-rise location
        j = start
        while j + 1 < n and float(d[j + 1]) > float(d[j]) + step_min:
            j += 1
        n_up = int(j - start)
        baseline = float(d[start - 1]) if start > 0 else float(d[start])
        rise = float(d[j] - baseline)
        is_hill = n_up > min_up and rise + 1e-12 >= float(min_rise)
        is_shelf = False
        if not is_hill and (float(d[start]) - baseline) + 1e-12 >= prominence:
            k = start
            while k + 1 < n and abs(float(d[k + 1]) - float(d[start])) <= shelf_eps:
                k += 1
            if int(k - start) > min_up:
                is_shelf = True
                j = k
        if not is_hill and not is_shelf:
            i = j + 1
            continue
        if is_hill:
            # Tiny leading ticks of a real hill can sit below the trailing
            # plateau, so D never returns under that first start. Advance to
            # the sudden-rise point that the downhill does fall below.
            peak = j
            while start <= peak - (min_up + 1) and not np.any(d[peak:] < d[start]):
                start += 1
        # End = first later point with D < start D, not D < previous D.
        base = float(d[start])
        end = j
        while end + 1 < n and float(d[end + 1]) >= base:
            end += 1
        if end + 1 < n and float(d[end + 1]) < base:
            end += 1
        rise_res = residual.copy()
        rise_res[start:end + 1] = np.maximum(
            residual[start:end + 1],
            d[start:end + 1] - baseline,
        )
        seg = _segment_stats(s, rise_res, start, end)
        seg.max_positive_deviation = max(seg.max_positive_deviation, float(np.max(d[start:end + 1]) - baseline))
        segments.append(seg)
        i = end + 1
    return _merge_overlapping(segments, s, residual)


def _trend_unreliable(
    diameter: np.ndarray,
    residual: np.ndarray,
    d_ref: np.ndarray,
    mad: float,
) -> Dict[str, Any]:
    d = np.asarray(diameter, dtype=float)
    r = np.asarray(residual, dtype=float)
    data_range = float(np.nanmax(d) - np.nanmin(d)) if d.size else 0.0
    ss_res = float(np.nansum((d - d_ref) ** 2))
    ss_tot = float(np.nansum((d - np.nanmean(d)) ** 2))
    fit_quality = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 1.0
    scatter = float(np.mean(np.abs(r) > (2.0 * MONOTONIC_NOISE_TOLERANCE_MM))) if r.size else 0.0
    dd = np.diff(d)
    if dd.size:
        step = float(OSCILLATION_SIGN_MIN_MM)
        sig = dd[np.round(np.abs(dd), int(DIAMETER_DECIMALS)) > step]
        if sig.size > 1:
            signs = np.sign(sig)
            signs[signs == 0] = 1
            sign_change_ratio = float(np.mean(np.diff(signs) != 0))
        else:
            sign_change_ratio = 0.0
    else:
        sign_change_ratio = 0.0

    reasons = []
    if gt(mad, TREND_MAD_MAX_MM):
        reasons.append("residual_mad_too_large")
    if gt(scatter, TREND_SCATTER_RATIO_MAX) and gt(data_range, 1.0):
        reasons.append("high_scatter")
    if fit_quality < TREND_FIT_QUALITY_MIN and gt(data_range, 2.0):
        reasons.append("unreliable_decreasing_trend")
    if gt(sign_change_ratio, 0.45) and gt(data_range, 2.0):
        reasons.append("oscillatory_profile")
    return {
        "unreliable": bool(reasons),
        "reasons": reasons,
        "fit_quality": fit_quality,
        "scatter_ratio": scatter,
        "sign_change_ratio": sign_change_ratio,
        "data_range": data_range,
    }


def validate_monotonic_profile(
    s: np.ndarray,
    diameter: Optional[np.ndarray] = None,
    circumference: Optional[np.ndarray] = None,
    *,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Detect local vs widespread deviations from a non-increasing OD trend.

    Parameters
    ----------
    s : cumulative arc length (mm)
    diameter / circumference : one of them is required. Decisions use OD.
    """
    s = np.asarray(s, dtype=float)
    if diameter is None:
        if circumference is None:
            raise ValueError("diameter or circumference is required")
        diameter = np.asarray(circumference, dtype=float) / np.pi
    else:
        diameter = np.asarray(diameter, dtype=float)

    diameter = quantize_diameter(diameter)

    cfg = {
        "rolling_median_window": ROLLING_MEDIAN_WINDOW,
        "noise_tolerance_mm": MONOTONIC_NOISE_TOLERANCE_MM,
        "residual_k": ANOMALY_RESIDUAL_K,
        "residual_abs_min_mm": ANOMALY_RESIDUAL_ABS_MIN_MM,
        "max_local_anomaly_point_ratio": MAX_LOCAL_ANOMALY_POINT_RATIO,
        "max_local_anomaly_length_ratio": MAX_LOCAL_ANOMALY_LENGTH_RATIO,
        "max_local_anomaly_segments": MAX_LOCAL_ANOMALY_SEGMENTS,
        "min_anomaly_segment_length_mm": MIN_ANOMALY_SEGMENT_LENGTH_MM,
        "local_rise_min_mm": LOCAL_RISE_MIN_MM,
        "local_rise_min_length_mm": LOCAL_RISE_MIN_LENGTH_MM,
        "local_rise_min_points": LOCAL_RISE_MIN_POINTS,
        "quantity": QA_QUANTITY,
        "algorithm_version": ALGORITHM_VERSION,
    }
    if params:
        cfg.update(params)

    n = int(s.size)
    if n == 0:
        return {
            "status": QA_MANUAL_REQUIRED,
            "anomaly_mask": [],
            "anomaly_indices": [],
            "suspect_segments": [],
            "anomaly_point_ratio": 0.0,
            "anomaly_length_ratio": 0.0,
            "reason": "empty_profile",
            "metrics": {"quantity": QA_QUANTITY},
            "reference": None,
        }

    window = int(cfg["rolling_median_window"])
    d_det = rolling_median(diameter, window)
    ref_full = isotonic_decreasing(d_det)
    residual = diameter - ref_full
    d_ref = ref_full
    abs_min = float(cfg["residual_abs_min_mm"])
    inliers = residual <= abs_min
    mad = _mad(residual[inliers]) if np.any(inliers) else _mad(residual)
    thresh = max(
        float(cfg["noise_tolerance_mm"]),
        float(cfg["residual_k"]) * mad,
        abs_min,
    )
    # Significant *upward* deviation from the decreasing reference.
    mask = residual > thresh
    trend = _trend_unreliable(diameter, residual, d_ref, mad)
    core_segments = _merge_runs(mask, s, residual)
    rise_segments = detect_local_rise_segments(
        s, diameter, residual,
        min_rise=float(cfg["local_rise_min_mm"]),
        min_length=float(cfg["local_rise_min_length_mm"]),
        min_up_points=int(cfg.get("local_rise_min_points", LOCAL_RISE_MIN_POINTS)),
    )

    total_length = float(s[-1] - s[0]) if n > 1 else 0.0
    bump_segments = expand_local_bumps(core_segments, s, diameter, residual)
    segments = _merge_overlapping(bump_segments + rise_segments, s, residual)

    # Widespread vs local uses residual-outlier fraction (10% cap) plus the
    # geometric bulge span (start at sudden rise, end at first D < start D).
    anomaly_points = int(np.count_nonzero(mask))
    point_ratio = float(anomaly_points / n) if n else 0.0
    anom_length = float(sum(max(seg.length, 0.0) for seg in segments))
    length_ratio = float(anom_length / total_length) if total_length > 0 else 0.0
    high_segs = [seg for seg in segments if seg.severity == "high"]
    decision = classify_local_vs_manual(
        has_anomaly=bool(np.any(mask)) or bool(segments),
        point_ratio=point_ratio,
        length_ratio=length_ratio,
        trend_unreliable=bool(trend["unreliable"]),
        trend_reasons=list(trend["reasons"]),
        segment_count=len(segments),
        high_severity_count=len(high_segs),
        max_point_ratio=float(cfg["max_local_anomaly_point_ratio"]),
        max_length_ratio=float(cfg["max_local_anomaly_length_ratio"]),
        max_segments=int(cfg["max_local_anomaly_segments"]),
    )
    status = decision["status"]
    reason = decision["reason"]
    local_points = decision["point_ratio_local_ok"]
    local_length = decision["length_ratio_local_ok"]
    if status == QA_LOCAL_ANOMALY and not segments:
        status = QA_MONOTONIC_PASS
        reason = "monotonic_non_increasing"
    ui_mask = np.zeros(n, dtype=bool)
    for seg in segments:
        ui_mask[seg.start_index:seg.end_index + 1] = True
    report_mask = ui_mask if segments else mask

    return {
        "status": status,
        "anomaly_mask": report_mask.astype(bool).tolist(),
        "anomaly_indices": [int(i) for i in np.flatnonzero(report_mask)],
        "suspect_segments": [seg.to_dict() for seg in segments],
        "anomaly_point_ratio": point_ratio,
        "anomaly_length_ratio": length_ratio,
        "reason": reason,
        "metrics": {
            "quantity": QA_QUANTITY,
            "n": n,
            "residual_threshold_mm": thresh,
            "residual_mad_mm": mad,
            "max_residual_mm": float(np.max(residual)) if residual.size else 0.0,
            "mean_residual_mm": float(np.mean(residual)) if residual.size else 0.0,
            "point_ratio_local_ok": local_points,
            "length_ratio_local_ok": local_length,
            "segment_count": len(segments),
            "core_segment_count": len(core_segments),
            "local_rise_count": len(rise_segments),
            "high_severity_segments": len([seg for seg in segments if seg.severity == "high"]),
            "trend": trend,
            "algorithm_version": ALGORITHM_VERSION,
            "params": cfg,
        },
        "reference": {
            "rolling_median": d_det.tolist(),
            "isotonic_decreasing": d_ref.tolist(),
            "residual": residual.tolist(),
        },
    }


def classify_local_vs_manual(
    *,
    has_anomaly: bool,
    point_ratio: float,
    length_ratio: float,
    trend_unreliable: bool,
    trend_reasons: Sequence[str],
    segment_count: int,
    high_severity_count: int,
    max_point_ratio: float = MAX_LOCAL_ANOMALY_POINT_RATIO,
    max_length_ratio: float = MAX_LOCAL_ANOMALY_LENGTH_RATIO,
    max_segments: int = MAX_LOCAL_ANOMALY_SEGMENTS,
) -> Dict[str, Any]:
    """<= 10% point ratio is still a local-review candidate; > 10% is manual.

    Equality uses ``ratio_leq`` so 0.10 is local and 0.10 + 1e-9 is manual.
    """
    local_points = ratio_leq(point_ratio, max_point_ratio)
    local_length = ratio_leq(length_ratio, max_length_ratio)
    too_many_segments = segment_count > max_segments

    if not has_anomaly and not trend_unreliable:
        return {
            "status": QA_MONOTONIC_PASS,
            "reason": "monotonic_non_increasing",
            "point_ratio_local_ok": True,
            "length_ratio_local_ok": True,
        }
    if trend_unreliable:
        reason = trend_reasons[0] if trend_reasons else "unreliable_decreasing_trend"
        return {
            "status": QA_MANUAL_REQUIRED,
            "reason": reason,
            "point_ratio_local_ok": local_points,
            "length_ratio_local_ok": local_length,
        }
    if not local_points:
        return {
            "status": QA_MANUAL_REQUIRED,
            "reason": "anomaly_point_ratio_exceeds_local_limit",
            "point_ratio_local_ok": False,
            "length_ratio_local_ok": local_length,
        }
    if not local_length:
        return {
            "status": QA_MANUAL_REQUIRED,
            "reason": "anomaly_length_ratio_exceeds_local_limit",
            "point_ratio_local_ok": local_points,
            "length_ratio_local_ok": False,
        }
    if too_many_segments and gt(point_ratio, 0.06):
        return {
            "status": QA_MANUAL_REQUIRED,
            "reason": "widespread_or_fragmented_anomaly",
            "point_ratio_local_ok": local_points,
            "length_ratio_local_ok": local_length,
        }
    if high_severity_count >= 3 and not local_length:
        return {
            "status": QA_MANUAL_REQUIRED,
            "reason": "widespread_or_fragmented_anomaly",
            "point_ratio_local_ok": local_points,
            "length_ratio_local_ok": local_length,
        }
    return {
        "status": QA_LOCAL_ANOMALY,
        "reason": "local_monotonicity_violation",
        "point_ratio_local_ok": local_points,
        "length_ratio_local_ok": local_length,
    }


def monotonic_pass(result: Dict[str, Any]) -> bool:
    return result.get("status") == QA_MONOTONIC_PASS
