"""Layer 1: basic legality checks and safe repairs for (s, C) profiles."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .config import (
    DUPLICATE_S_MERGE_EPS_MM,
    MAX_DIAMETER_MM,
    MAX_MISSING_RATIO,
    MAX_SAMPLE_GAP_MM,
    MIN_CENTERLINE_LENGTH_MM,
    MIN_DIAMETER_MM,
    MIN_PROFILE_POINTS,
)
from .models import ProfileArrays
from .numeric import gt, lt
from .state import QA_INVALID_INPUT, QA_PENDING


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def circumference_to_diameter(c: np.ndarray) -> np.ndarray:
    return np.asarray(c, dtype=float) / np.pi


def diameter_to_circumference(d: np.ndarray) -> np.ndarray:
    return np.asarray(d, dtype=float) * np.pi


def records_to_arrays(
    records: Sequence[dict],
    *,
    s_key: str = "中心线长度(mm)",
    c_key: str = "外径周长(mm)",
) -> Tuple[np.ndarray, np.ndarray]:
    s_list: List[float] = []
    c_list: List[float] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            s_list.append(np.nan)
            c_list.append(np.nan)
            continue
        try:
            s_list.append(float(rec.get(s_key)))
        except (TypeError, ValueError):
            s_list.append(np.nan)
        try:
            c_list.append(float(rec.get(c_key)))
        except (TypeError, ValueError):
            c_list.append(np.nan)
    return np.asarray(s_list, dtype=float), np.asarray(c_list, dtype=float)


def validate_profile_input(
    s: np.ndarray,
    circumference: np.ndarray,
    *,
    centerline_length: Optional[float] = None,
    geometry_error: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a structured validation result; never raises on numeric junk."""
    log: List[dict] = []
    warnings: List[str] = []
    s = np.asarray(s, dtype=float).copy()
    c = np.asarray(circumference, dtype=float).copy()

    if geometry_error:
        return _invalid("STP 几何解析失败", "geometry_parse_failed", log, extra={"geometry_error": geometry_error})

    n_raw = int(s.size)
    if n_raw == 0 or c.size == 0:
        return _invalid("形线数据为空", "empty_profile", log)
    if s.size != c.size:
        return _invalid("轴向坐标与周长点数不一致", "length_mismatch", log)

    finite_s = np.isfinite(s)
    finite_c = np.isfinite(c)
    n_nan_s = int((~finite_s).sum())
    n_nan_c = int((~finite_c).sum())
    n_inf_s = int(np.isinf(s).sum()) if n_raw else 0
    n_inf_c = int(np.isinf(c).sum()) if n_raw else 0

    if n_nan_s or n_inf_s or n_nan_c or n_inf_c:
        missing_ratio = float((~finite_s | ~finite_c).mean())
        if gt(missing_ratio, MAX_MISSING_RATIO) or n_inf_s or n_inf_c:
            return _invalid(
                "存在 NaN/Inf 或大量缺失点，无法安全修复",
                "nan_inf_or_missing",
                log,
                extra={
                    "nan_s": n_nan_s,
                    "nan_c": n_nan_c,
                    "inf_s": n_inf_s,
                    "inf_c": n_inf_c,
                    "missing_ratio": missing_ratio,
                },
            )
        keep = finite_s & finite_c
        dropped = int((~keep).sum())
        s, c = s[keep], c[keep]
        log.append({"fix": "drop_nonfinite", "dropped": dropped})
        warnings.append(f"已删除 {dropped} 个非有限采样点")

    if int(s.size) < MIN_PROFILE_POINTS:
        return _invalid(
            f"有效采样点不足（{int(s.size)} < {MIN_PROFILE_POINTS}）",
            "too_few_points",
            log,
        )

    d = circumference_to_diameter(c)
    if np.any(c < 0) or np.any(d < 0):
        return _invalid("存在负的周长或外径", "negative_size", log)

    impossible = (d < MIN_DIAMETER_MM) | (d > MAX_DIAMETER_MM) | (c <= 0)
    if np.any(impossible):
        n_bad = int(impossible.sum())
        if gt(n_bad / max(len(d), 1), MAX_MISSING_RATIO):
            return _invalid(
                "存在明显不可能的尺寸（外径超出物理范围）",
                "nonphysical_size",
                log,
                extra={"bad_count": n_bad},
            )
        s, c, d = s[~impossible], c[~impossible], d[~impossible]
        log.append({"fix": "drop_nonphysical", "dropped": n_bad})
        warnings.append(f"已删除 {n_bad} 个非物理尺寸点")

    if int(s.size) < MIN_PROFILE_POINTS:
        return _invalid("删除非法点后采样点不足", "too_few_points_after_clean", log)

    # Sort by s (safe).
    if np.any(np.diff(s) < 0):
        order = np.argsort(s, kind="mergesort")
        s, c, d = s[order], c[order], d[order]
        log.append({"fix": "sort_by_s"})
        warnings.append("采样位置未按累计弧长排序，已自动排序")

    # Merge duplicate / non-strictly-increasing s.
    merged = _merge_duplicate_s(s, c)
    if merged is None:
        return _invalid("存在无法合并的重复采样位置", "duplicate_s", log)
    s, c, n_merged = merged
    d = circumference_to_diameter(c)
    if n_merged:
        log.append({"fix": "merge_duplicate_s", "merged_groups": n_merged})
        warnings.append(f"已合并 {n_merged} 组重复采样位置（周长取平均）")

    if int(s.size) < MIN_PROFILE_POINTS:
        return _invalid("合并重复点后采样点不足", "too_few_points_after_merge", log)

    diffs = np.diff(s)
    if np.any(diffs <= 0):
        return _invalid("轴向坐标 s 未能严格递增", "s_not_strictly_increasing", log)

    max_gap = float(np.max(diffs)) if diffs.size else 0.0
    if gt(max_gap, MAX_SAMPLE_GAP_MM):
        warnings.append(f"存在异常大的采样间隔 {max_gap:.2f} mm")
        log.append({"warning": "large_sample_gap", "max_gap_mm": max_gap})

    total_s = float(s[-1] - s[0]) if s.size else 0.0
    clen = float(centerline_length) if centerline_length is not None else total_s
    try:
        clen_ok = np.isfinite(clen) and not lt(clen, MIN_CENTERLINE_LENGTH_MM)
    except (TypeError, ValueError):
        clen_ok = False
    if not clen_ok or not np.isfinite(total_s) or not (total_s > 0):
        return _invalid("中心线长度无效或为 0", "invalid_centerline_length", log)

    profile = ProfileArrays(s=s, circumference=c, diameter=d, source="stp_raw")
    return {
        "ok": True,
        "status": QA_PENDING,
        "profile": profile,
        "warnings": warnings,
        "log": log,
        "metrics": {
            "point_count": int(s.size),
            "raw_point_count": n_raw,
            "s_min": float(s[0]),
            "s_max": float(s[-1]),
            "total_length_mm": float(total_s),
            "centerline_length_mm": float(clen),
            "max_sample_gap_mm": max_gap,
            "diameter_min": float(np.min(d)),
            "diameter_max": float(np.max(d)),
            "quantity": "outer_diameter_mm",
        },
    }


def _merge_duplicate_s(
    s: np.ndarray, c: np.ndarray
) -> Optional[Tuple[np.ndarray, np.ndarray, int]]:
    if s.size == 0:
        return s, c, 0
    s_out = [float(s[0])]
    c_acc = [float(c[0])]
    w_acc = [1]
    groups = 0
    for i in range(1, len(s)):
        if abs(float(s[i]) - s_out[-1]) <= DUPLICATE_S_MERGE_EPS_MM:
            c_acc[-1] += float(c[i])
            w_acc[-1] += 1
            groups += 1
        else:
            s_out.append(float(s[i]))
            c_acc.append(float(c[i]))
            w_acc.append(1)
    c_out = [cv / w for cv, w in zip(c_acc, w_acc)]
    return np.asarray(s_out, dtype=float), np.asarray(c_out, dtype=float), groups


def _invalid(message: str, reason: str, log: List[dict], extra: Optional[dict] = None) -> dict:
    payload = {
        "ok": False,
        "status": QA_INVALID_INPUT,
        "profile": None,
        "warnings": [],
        "log": log + [{"error": reason, "at": _now_iso()}],
        "reason": reason,
        "message": message,
        "metrics": extra or {},
        "code": QA_INVALID_INPUT,
    }
    return payload
