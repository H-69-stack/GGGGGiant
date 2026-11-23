"""Piecewise constant + PCHIP resampling. Independent of STP smoothing."""
from __future__ import annotations

from typing import Any, Dict, Sequence

import numpy as np

from .config import MONOTONE_EPS_MM, SAMPLE_STEP_MM


def resample_manual_profile(
    x: np.ndarray,
    y: np.ndarray,
    segments: Sequence[Dict[str, Any]],
    *,
    step_mm: float = SAMPLE_STEP_MM,
) -> Dict[str, Any]:
    """Evaluate a dense formline on the real (possibly uneven) knot locations."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 2:
        raise ValueError("PCHIP 至少需要 2 个控制点。")
    grid = _sample_axis(x, float(step_mm))
    d = _pchip_evaluate(x, y, grid)
    d = _apply_constant_segments(grid, d, segments)
    d = _enforce_monotone(d)
    return {"s": grid, "diameter": d}


def _sample_axis(x: np.ndarray, step_mm: float) -> np.ndarray:
    x0 = float(x[0])
    x1 = float(x[-1])
    step = max(float(step_mm), 1e-6)
    regular = np.arange(x0, x1 + 0.5 * step, step, dtype=float)
    if regular.size == 0 or abs(regular[-1] - x1) > 1e-9:
        regular = np.append(regular, x1)
    axis = np.unique(np.concatenate([regular, np.asarray(x, dtype=float)]))
    axis[0] = x0
    axis[-1] = x1
    return axis


def _pchip_evaluate(x: np.ndarray, y: np.ndarray, grid: np.ndarray) -> np.ndarray:
    from scipy.interpolate import PchipInterpolator

    interpolator = PchipInterpolator(x, y, extrapolate=False)
    values = np.asarray(interpolator(grid), dtype=float)
    # Endpoints must stay on the knots even if the grid clips.
    values[0] = float(y[0])
    values[-1] = float(y[-1])
    return values


def _apply_constant_segments(
    s: np.ndarray,
    d: np.ndarray,
    segments: Sequence[Dict[str, Any]],
) -> np.ndarray:
    out = np.asarray(d, dtype=float).copy()
    for seg in segments or []:
        if seg.get("kind") != "constant":
            continue
        x0 = float(seg["start_x"])
        x1 = float(seg["end_x"])
        level = 0.5 * (float(seg["start_y"]) + float(seg["end_y"]))
        mask = (s >= x0 - 1e-12) & (s <= x1 + 1e-12)
        out[mask] = level
    return out


def _enforce_monotone(d: np.ndarray) -> np.ndarray:
    out = np.asarray(d, dtype=float).copy()
    for i in range(1, len(out)):
        if out[i] > out[i - 1] + MONOTONE_EPS_MM:
            out[i] = out[i - 1]
    return out
