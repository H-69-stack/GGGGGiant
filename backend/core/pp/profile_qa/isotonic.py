"""Decreasing isotonic regression (PAVA) used only as a trend reference."""
from __future__ import annotations

from typing import Optional

import numpy as np


def pava_increasing(y: np.ndarray, weights: Optional[np.ndarray] = None) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    n = int(y.size)
    if n == 0:
        return y.copy()
    if weights is None:
        w = np.ones(n, dtype=float)
    else:
        w = np.asarray(weights, dtype=float)
        w = np.where(np.isfinite(w) & (w > 0), w, 1.0)
    val: list[float] = []
    wt: list[float] = []
    cnt: list[int] = []
    for i in range(n):
        val.append(float(y[i]))
        wt.append(float(w[i]))
        cnt.append(1)
        while len(val) >= 2 and val[-2] > val[-1]:
            v2 = val.pop()
            v1 = val.pop()
            w2 = wt.pop()
            w1 = wt.pop()
            c2 = cnt.pop()
            c1 = cnt.pop()
            tw = w1 + w2
            val.append((v1 * w1 + v2 * w2) / tw)
            wt.append(tw)
            cnt.append(c1 + c2)
    out = np.empty(n, dtype=float)
    idx = 0
    for v, c in zip(val, cnt):
        out[idx:idx + c] = v
        idx += c
    return out


def isotonic_decreasing(y: np.ndarray, weights: Optional[np.ndarray] = None) -> np.ndarray:
    """Non-increasing projection. Never used to silently overwrite raw profiles."""
    y = np.asarray(y, dtype=float)
    w = None if weights is None else np.asarray(weights, dtype=float)
    return -pava_increasing(-y, w)
