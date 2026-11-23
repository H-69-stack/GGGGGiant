"""Parse engineer control points without mutating the original list."""
from __future__ import annotations

from typing import Any, List, Sequence, Tuple

import numpy as np

from .config import MAX_DIAMETER_MM, MIN_CONTROL_POINTS, MIN_DIAMETER_MM


class ManualProfileError(ValueError):
    def __init__(self, message: str, *, code: str = "MANUAL_PROFILE_INVALID"):
        super().__init__(message)
        self.code = code
        self.message = message


def copy_raw_xlxs(xlxs: Sequence[Any]) -> List[List[float]]:
    """Deep-copy the engineer input as [[x, y], ...]. Never returns the same list."""
    out: List[List[float]] = []
    if xlxs is None:
        return out
    for row in xlxs:
        if row is None:
            out.append([])
            continue
        try:
            out.append([row[0], row[1]])
        except (TypeError, IndexError, KeyError):
            out.append(list(row) if isinstance(row, (list, tuple)) else [row])
    return out


def validate_raw_xlxs(
    xlxs: Sequence[Any],
    *,
    check_diameter: bool = True,
) -> Tuple[np.ndarray, np.ndarray]:
    """Require >= 2 finite numeric (x, y) pairs. y is outer diameter mm."""
    if xlxs is None:
        raise ManualProfileError("人工形线控制点为空。", code="empty")
    xs: List[float] = []
    ys: List[float] = []
    for i, row in enumerate(xlxs):
        if row is None:
            raise ManualProfileError(f"第 {i + 1} 个控制点为空。", code="null_point")
        try:
            x_raw, y_raw = row[0], row[1]
        except (TypeError, IndexError, KeyError) as exc:
            raise ManualProfileError(
                f"第 {i + 1} 个控制点必须是 [x, y]。",
                code="bad_shape",
            ) from exc
        x = _finite_float(x_raw, f"第 {i + 1} 个控制点的 x")
        y = _finite_float(y_raw, f"第 {i + 1} 个控制点的 y")
        if check_diameter and (y < MIN_DIAMETER_MM or y > MAX_DIAMETER_MM):
            raise ManualProfileError(
                f"第 {i + 1} 个控制点外径 {y:.3f} mm 超出允许范围 "
                f"{MIN_DIAMETER_MM}–{MAX_DIAMETER_MM} mm。",
                code="nonphysical_y",
            )
        xs.append(x)
        ys.append(y)
    if len(xs) < MIN_CONTROL_POINTS:
        raise ManualProfileError(
            f"有效控制点 {len(xs)} 个，至少需要 {MIN_CONTROL_POINTS} 个。",
            code="too_few_points",
        )
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)


def _finite_float(value: Any, label: str) -> float:
    if value is None:
        raise ManualProfileError(f"{label} 不能为空。", code="nan_or_inf")
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            raise ManualProfileError(f"{label} 不能为空。", code="nan_or_inf")
        value = text
    try:
        num = float(value)
    except (TypeError, ValueError) as exc:
        raise ManualProfileError(f"{label} = {value!r}，必须是有限数值。", code="not_numeric") from exc
    if not np.isfinite(num):
        raise ManualProfileError(f"{label} 不能是 NaN/Inf。", code="nan_or_inf")
    return float(num)
