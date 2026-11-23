"""Wall-thickness confirmation: T_i requires TL_i, drawing length = Σ length fields."""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

from .config import WALL_LENGTH_KEYS, WALL_THICKNESS_LENGTH_PAIRS


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not str(value).strip():
        return True
    return False


def parse_numeric_field(value: Any) -> Tuple[Optional[float], Optional[str]]:
    """Return (number, error). Missing → (None, None). Invalid → (None, 'invalid')."""
    if _is_blank(value):
        return None, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "invalid"
    if not math.isfinite(number):
        return None, "invalid"
    return number, None


def validate_wall_thickness(values: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Require TL_i whenever T_i is present, including the single-segment T1/TL1 case.

    Empty / missing length fields do not contribute to drawing length.
    A present thickness with missing, zero, or negative TL is a hard error.
    """
    payload = dict(values or {})
    parsed: Dict[str, Optional[float]] = {}
    errors = []

    for t_key, tl_key in WALL_THICKNESS_LENGTH_PAIRS:
        t_val, t_err = parse_numeric_field(payload.get(t_key))
        tl_val, tl_err = parse_numeric_field(payload.get(tl_key))
        parsed[t_key] = t_val
        parsed[tl_key] = tl_val

        t_present = t_val is not None and t_err is None and t_val > 0
        if t_err == "invalid":
            errors.append(f"壁厚 {t_key} 必须为有效数值。")
            continue
        if not t_present:
            if tl_err == "invalid":
                errors.append(f"壁厚长度 {tl_key} 必须为大于 0 的数值。")
            continue

        if tl_err == "invalid" or tl_val is None or tl_val <= 0:
            errors.append(f"请填写壁厚 {t_key} 对应的长度 {tl_key}。")

    for key in WALL_LENGTH_KEYS:
        if key in parsed:
            continue
        val, err = parse_numeric_field(payload.get(key))
        parsed[key] = val
        if err == "invalid" or (val is not None and val < 0):
            errors.append(f"壁厚长度 {key} 必须为大于 0 的数值。")

    ok = not errors
    return {
        "ok": ok,
        "errors": errors,
        "message": errors[0] if errors else "",
        "parsed": parsed,
    }


def calculate_drawing_length(values: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Sum every valid wall-thickness length field (TL* and TLM*)."""
    check = validate_wall_thickness(values)
    if not check["ok"]:
        return {
            "ok": False,
            "drawing_length": None,
            "terms": {},
            "errors": check["errors"],
            "message": check["message"],
        }

    terms: Dict[str, float] = {}
    total = 0.0
    parsed = check["parsed"]
    for key in WALL_LENGTH_KEYS:
        val = parsed.get(key)
        if val is None:
            continue
        if val < 0:
            return {
                "ok": False,
                "drawing_length": None,
                "terms": {},
                "errors": [f"壁厚长度 {key} 必须为大于 0 的数值。"],
                "message": f"壁厚长度 {key} 必须为大于 0 的数值。",
            }
        if val > 0:
            terms[key] = float(val)
            total += float(val)

    if total <= 0:
        msg = "请填写壁厚 T1 对应的长度 TL1。"
        return {
            "ok": False,
            "drawing_length": None,
            "terms": {},
            "errors": [msg],
            "message": msg,
        }

    return {
        "ok": True,
        "drawing_length": float(total),
        "terms": terms,
        "errors": [],
        "message": "",
        "parsed": parsed,
    }
