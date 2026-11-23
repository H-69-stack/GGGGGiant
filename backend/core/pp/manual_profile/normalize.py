"""Sort, merge duplicates, shift x to 0, unify large-to-small as x increases."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .config import CONSTANT_Y_TOLERANCE_MM, DUPLICATE_X_EPS_MM
from .validate import ManualProfileError


def normalize_control_points(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """Return sorted, unique, direction-unified knots. Does not touch the raw arrays."""
    x = np.asarray(x, dtype=float).copy()
    y = np.asarray(y, dtype=float).copy()
    order = np.argsort(x, kind="mergesort")
    x, y = x[order], y[order]
    x, y, n_merged = _merge_duplicate_x(x, y)
    if x.size < 2:
        raise ManualProfileError("合并重复位置后控制点不足 2 个。", code="too_few_after_merge")
    original_x0 = float(x[0])
    original_x1 = float(x[-1])
    x = x - original_x0
    reversed_orientation = bool(float(y[-1]) > float(y[0]) + CONSTANT_Y_TOLERANCE_MM)
    if reversed_orientation:
        span = float(x[-1])
        x = span - x[::-1]
        y = y[::-1]
    points = [[float(xi), float(yi)] for xi, yi in zip(x, y)]
    return {
        "x": x,
        "y": y,
        "points": points,
        "reversed": reversed_orientation,
        "merged_groups": int(n_merged),
        "original_x0": original_x0,
        "original_x1": original_x1,
        "sorted": True,
    }


def _merge_duplicate_x(x: np.ndarray, y: np.ndarray) -> tuple:
    xs: List[float] = [float(x[0])]
    acc: List[float] = [float(y[0])]
    w: List[int] = [1]
    groups = 0
    for i in range(1, len(x)):
        if abs(float(x[i]) - xs[-1]) <= DUPLICATE_X_EPS_MM:
            acc[-1] += float(y[i])
            w[-1] += 1
            groups += 1
        else:
            xs.append(float(x[i]))
            acc.append(float(y[i]))
            w.append(1)
    ys = [a / wi for a, wi in zip(acc, w)]
    return np.asarray(xs, dtype=float), np.asarray(ys, dtype=float), groups
