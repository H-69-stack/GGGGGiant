"""Identify straight plateaus and enforce monotonic non-increasing control y."""
from __future__ import annotations

from typing import Any, Dict, List

import numpy as np

from .config import CONSTANT_Y_TOLERANCE_MM, MONOTONE_EPS_MM


def constrain_control_points(x: np.ndarray, y: np.ndarray) -> Dict[str, Any]:
    """Flatten same-size runs, then clip any local rise so y is non-increasing."""
    x = np.asarray(x, dtype=float).copy()
    y = np.asarray(y, dtype=float).copy()
    y, plateaus = _flatten_plateaus(y)
    n_clipped = 0
    for i in range(1, len(y)):
        if y[i] > y[i - 1] + MONOTONE_EPS_MM:
            y[i] = y[i - 1]
            n_clipped += 1
    segments = _label_segments(x, y)
    return {
        "x": x,
        "y": y,
        "points": [[float(xi), float(yi)] for xi, yi in zip(x, y)],
        "plateaus": plateaus,
        "segments": segments,
        "n_clipped": n_clipped,
    }


def _flatten_plateaus(y: np.ndarray) -> tuple:
    y = np.asarray(y, dtype=float).copy()
    n = int(y.size)
    plateaus: List[Dict[str, Any]] = []
    i = 0
    while i < n:
        j = i
        lo = hi = float(y[i])
        while j + 1 < n:
            cand = float(y[j + 1])
            new_lo = min(lo, cand)
            new_hi = max(hi, cand)
            if new_hi - new_lo > CONSTANT_Y_TOLERANCE_MM:
                break
            lo, hi = new_lo, new_hi
            j += 1
        if j > i:
            level = float(np.median(y[i : j + 1]))
            y[i : j + 1] = level
            plateaus.append({"start": i, "end": j, "y": level})
        i = j + 1
    return y, plateaus


def _label_segments(x: np.ndarray, y: np.ndarray) -> List[Dict[str, Any]]:
    segs: List[Dict[str, Any]] = []
    for i in range(len(x) - 1):
        constant = abs(float(y[i + 1]) - float(y[i])) <= CONSTANT_Y_TOLERANCE_MM
        segs.append({
            "start_x": float(x[i]),
            "end_x": float(x[i + 1]),
            "start_y": float(y[i]),
            "end_y": float(y[i + 1]),
            "kind": "constant" if constant else "taper",
        })
    return segs
