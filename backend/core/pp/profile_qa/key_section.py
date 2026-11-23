"""Search and persist key-section checks against the current effective formline."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from core.pp.tube.geometry import principal_extents_2d

from .config import SECTION_MATCH_TOLERANCE_MM, SECTION_RANGE_GAP_MM


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def section_size_from_outer_2d(pts: Any) -> Optional[tuple]:
    """Principal-axis length × width (mm) of a section outer contour."""
    return principal_extents_2d(pts)


def xy_from_section_row(row: dict) -> Optional[tuple]:
    """Prefer stored x_length/y_length; otherwise compute from outer_2d."""
    if not isinstance(row, dict):
        return None
    x = row.get("x_length")
    y = row.get("y_length")
    if x is None:
        x = row.get("截面长度(mm)")
    if y is None:
        y = row.get("截面宽度(mm)")
    try:
        if x is not None and y is not None:
            xf = float(x)
            yf = float(y)
            if math.isfinite(xf) and math.isfinite(yf) and xf > 0 and yf > 0:
                return xf, yf
    except (TypeError, ValueError):
        pass
    return section_size_from_outer_2d(row.get("outer_2d") or row.get("hull_2d"))


def records_with_section_sizes(
    records: Sequence[dict],
    extra_sections: Optional[Sequence[dict]] = None,
) -> List[dict]:
    """Copy records and fill x_length/y_length from themselves or sections_full."""
    extra_by_s: Dict[float, tuple] = {}
    for sf in extra_sections or []:
        if not isinstance(sf, dict):
            continue
        size = xy_from_section_row(sf)
        if not size:
            continue
        try:
            key = round(float(sf.get("cumulative_length", sf.get("中心线长度(mm)", 0)) or 0), 2)
        except (TypeError, ValueError):
            continue
        extra_by_s[key] = size
    out: List[dict] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        item = dict(rec)
        size = xy_from_section_row(item)
        if size is None:
            try:
                key = round(float(item.get("中心线长度(mm)", item.get("cumulative_length", 0)) or 0), 2)
            except (TypeError, ValueError):
                key = None
            if key is not None:
                size = extra_by_s.get(key)
        if size:
            item["x_length"] = round(float(size[0]), 4)
            item["y_length"] = round(float(size[1]), 4)
        out.append(item)
    return out


def section_dims_usable(dims: Optional[dict]) -> bool:
    if not isinstance(dims, dict):
        return False
    s = dims.get("s_mm") or []
    x = dims.get("x_length_mm") or []
    y = dims.get("y_length_mm") or []
    return len(s) >= 2 and len(x) >= 2 and len(y) >= 2 and len(s) == len(x) == len(y)


def extract_section_dims(records: Sequence[dict], s_mm: Optional[Sequence[float]] = None) -> Optional[dict]:
    """Pull section length/width from existing PP records without renaming keys."""
    if not records:
        return None
    s_list: List[float] = []
    x_list: List[float] = []
    y_list: List[float] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        size = xy_from_section_row(rec)
        if not size:
            continue
        try:
            xf, yf = float(size[0]), float(size[1])
            sf = float(rec.get("中心线长度(mm)", rec.get("cumulative_length", 0)) or 0)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(xf) and math.isfinite(yf) and math.isfinite(sf)):
            continue
        s_list.append(sf)
        x_list.append(xf)
        y_list.append(yf)
    if len(s_list) < 2:
        return None
    s_arr = np.asarray(s_list, dtype=float)
    x_arr = np.asarray(x_list, dtype=float)
    y_arr = np.asarray(y_list, dtype=float)
    if s_mm is not None and len(s_mm) >= 2:
        s_new = np.asarray(s_mm, dtype=float)
        x_arr = np.interp(s_new, s_arr, x_arr)
        y_arr = np.interp(s_new, s_arr, y_arr)
        s_arr = s_new
    length = np.maximum(x_arr, y_arr)
    width = np.minimum(x_arr, y_arr)
    return {
        "s_mm": s_arr.tolist(),
        "x_length_mm": x_arr.tolist(),
        "y_length_mm": y_arr.tolist(),
        "length_mm": length.tolist(),
        "width_mm": width.tolist(),
    }


def _pair_error(actual_a: float, actual_b: float, target_l: float, target_w: float) -> Dict[str, float]:
    e1 = math.hypot(actual_a - target_l, actual_b - target_w)
    e2 = math.hypot(actual_a - target_w, actual_b - target_l)
    if e1 <= e2:
        return {
            "error": e1,
            "actual_length": actual_a,
            "actual_width": actual_b,
            "length_delta": actual_a - target_l,
            "width_delta": actual_b - target_w,
        }
    return {
        "error": e2,
        "actual_length": actual_b,
        "actual_width": actual_a,
        "length_delta": actual_b - target_l,
        "width_delta": actual_a - target_w,
    }


def _within_tol(delta_l: float, delta_w: float, tolerance: float) -> bool:
    return abs(delta_l) <= tolerance + 1e-15 and abs(delta_w) <= tolerance + 1e-15


def _parse_expected_s(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        expected = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(expected):
        return None
    return expected


def search_key_sections(
    section_dims: Optional[dict],
    target_length: float,
    target_width: float,
    *,
    tolerance_mm: float = SECTION_MATCH_TOLERANCE_MM,
    extra: Optional[Sequence[dict]] = None,
    expected_s_mm: Optional[float] = None,
) -> Dict[str, Any]:
    """Return every candidate within tolerance, sorted by combined size error."""
    try:
        L = float(target_length)
        W = float(target_width)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "candidates": [],
            "message": "请输入有效的截面长度和截面宽度。",
        }
    if not (math.isfinite(L) and math.isfinite(W)) or L <= 0 or W <= 0:
        return {
            "ok": False,
            "candidates": [],
            "message": "请输入有效的截面长度和截面宽度。",
        }
    if not section_dims or not section_dims.get("s_mm"):
        return {
            "ok": True,
            "candidates": [],
            "message": "当前截面缺少长度/宽度数据。请重新读取 STP 后再搜索。",
            "target_length": L,
            "target_width": W,
            "tolerance_mm": float(tolerance_mm),
        }

    s = np.asarray(section_dims.get("s_mm") or [], dtype=float)
    x = np.asarray(section_dims.get("x_length_mm") or [], dtype=float)
    y = np.asarray(section_dims.get("y_length_mm") or [], dtype=float)
    n = int(min(s.size, x.size, y.size))
    candidates: List[dict] = []
    for i in range(n):
        paired = _pair_error(float(x[i]), float(y[i]), L, W)
        if not _within_tol(paired["length_delta"], paired["width_delta"], float(tolerance_mm)):
            continue
        item = {
            "index": i,
            "s_mm": float(s[i]),
            "actual_length": round(paired["actual_length"], 4),
            "actual_width": round(paired["actual_width"], 4),
            "length_delta": round(paired["length_delta"], 4),
            "width_delta": round(paired["width_delta"], 4),
            "error": round(paired["error"], 6),
            "x_length_mm": round(float(x[i]), 4),
            "y_length_mm": round(float(y[i]), 4),
        }
        if extra and i < len(extra) and isinstance(extra[i], dict):
            for key in ("station_id", "section_id", "id"):
                if extra[i].get(key) is not None:
                    item[key] = extra[i].get(key)
        candidates.append(item)
    ranges = merge_candidates_to_ranges(candidates, gap_mm=SECTION_RANGE_GAP_MM)
    expected = _parse_expected_s(expected_s_mm)
    if expected is not None:
        for item in ranges:
            actual = float(item.get("s_mm") or 0)
            item["expected_s_mm"] = round(expected, 4)
            item["position_delta"] = round(actual - expected, 4)
    if not candidates:
        message = "在当前尺寸容差范围内未找到匹配截面。"
    elif len(candidates) > len(ranges):
        message = f"找到 {len(candidates)} 个匹配截面，已合并为 {len(ranges)} 段。"
    else:
        message = ""
    return {
        "ok": True,
        "candidates": ranges,
        "message": message,
        "target_length": L,
        "target_width": W,
        "tolerance_mm": float(tolerance_mm),
        "count": len(candidates),
        "range_count": len(ranges),
        "expected_s_mm": expected,
    }


def merge_candidates_to_ranges(
    candidates: Sequence[dict],
    *,
    gap_mm: float = SECTION_RANGE_GAP_MM,
) -> List[dict]:
    """Collapse consecutive matched stations into axial ranges."""
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda row: float(row.get("s_mm") or 0))
    groups: List[List[dict]] = []
    current = [ordered[0]]
    for item in ordered[1:]:
        prev_s = float(current[-1].get("s_mm") or 0)
        this_s = float(item.get("s_mm") or 0)
        if this_s - prev_s <= float(gap_mm) + 1e-9:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    groups.append(current)
    ranges: List[dict] = []
    for group in groups:
        best = min(group, key=lambda row: (float(row.get("error") or 0), abs(float(row.get("s_mm") or 0))))
        s0 = float(group[0].get("s_mm") or 0)
        s1 = float(group[-1].get("s_mm") or 0)
        item = dict(best)
        item["s_mm"] = float(best.get("s_mm") or 0)
        item["s_start_mm"] = round(s0, 4)
        item["s_end_mm"] = round(s1, 4)
        item["point_count"] = len(group)
        ranges.append(item)
    ranges.sort(key=lambda row: (float(row.get("error") or 0), abs(float(row.get("s_start_mm") or 0))))
    for i, item in enumerate(ranges):
        item["range_index"] = i
    return ranges


def next_record_id(records: Sequence[dict]) -> int:
    used = [int(r.get("id") or 0) for r in records or [] if str(r.get("id") or "").isdigit() or isinstance(r.get("id"), int)]
    return (max(used) + 1) if used else 1


def build_saved_record(
    target_length: float,
    target_width: float,
    candidate: dict,
    *,
    record_id: int,
    profile_version: str,
    profile_signature: str,
    expected_s_mm: Optional[float] = None,
) -> dict:
    s_mm = float(candidate.get("s_mm"))
    s_start = candidate.get("s_start_mm")
    s_end = candidate.get("s_end_mm")
    try:
        s_start_f = float(s_start) if s_start is not None else s_mm
        s_end_f = float(s_end) if s_end is not None else s_mm
    except (TypeError, ValueError):
        s_start_f = s_mm
        s_end_f = s_mm
    try:
        point_count = int(candidate.get("point_count") or 1)
    except (TypeError, ValueError):
        point_count = 1
    expected = _parse_expected_s(
        expected_s_mm if expected_s_mm is not None else candidate.get("expected_s_mm"),
    )
    position_delta = None if expected is None else round(s_mm - expected, 4)
    return {
        "id": int(record_id),
        "target_length": float(target_length),
        "target_width": float(target_width),
        "s_mm": s_mm,
        "s_start_mm": round(s_start_f, 4),
        "s_end_mm": round(s_end_f, 4),
        "point_count": max(1, point_count),
        "actual_length": float(candidate.get("actual_length")),
        "actual_width": float(candidate.get("actual_width")),
        "length_delta": float(candidate.get("length_delta")),
        "width_delta": float(candidate.get("width_delta")),
        "error": float(candidate.get("error") or 0),
        "expected_s_mm": None if expected is None else round(expected, 4),
        "position_delta": position_delta,
        "stale": False,
        "profile_version": profile_version,
        "profile_signature": profile_signature,
        "saved_at": _now(),
    }


def profile_signature(profile: Optional[dict]) -> str:
    if not isinstance(profile, dict):
        return ""
    s = profile.get("s_mm") or []
    if not s:
        return ""
    try:
        return f"{len(s)}:{round(float(s[0]), 4)}:{round(float(s[-1]), 4)}"
    except (TypeError, ValueError, IndexError):
        return str(len(s))


def mark_records_stale(records: Optional[List[dict]], *, reason: str = "") -> List[dict]:
    out: List[dict] = []
    for rec in records or []:
        item = dict(rec)
        item["stale"] = True
        if reason:
            item["stale_reason"] = reason
        out.append(item)
    return out
