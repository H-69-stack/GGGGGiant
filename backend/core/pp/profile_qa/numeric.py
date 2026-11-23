"""Unified floating-point comparison helpers for profile QA thresholds."""
from __future__ import annotations

import math
from typing import Union

from .config import EPS, RATIO_EPS, THRESHOLD_EPS

Number = Union[int, float]


def isclose(a: Number, b: Number, *, atol: float = EPS, rtol: float = 0.0) -> bool:
    try:
        return math.isclose(float(a), float(b), rel_tol=rtol, abs_tol=atol)
    except (TypeError, ValueError):
        return False


def leq(a: Number, b: Number, *, atol: float = EPS) -> bool:
    """True iff a <= b, treating values within atol as equal."""
    fa, fb = float(a), float(b)
    return fa < fb or isclose(fa, fb, atol=atol)


def geq(a: Number, b: Number, *, atol: float = EPS) -> bool:
    fa, fb = float(a), float(b)
    return fa > fb or isclose(fa, fb, atol=atol)


def lt(a: Number, b: Number, *, atol: float = EPS) -> bool:
    """Strict less-than after equality band."""
    fa, fb = float(a), float(b)
    return fa < fb and not isclose(fa, fb, atol=atol)


def gt(a: Number, b: Number, *, atol: float = EPS) -> bool:
    fa, fb = float(a), float(b)
    return fa > fb and not isclose(fa, fb, atol=atol)


def ratio_leq(ratio: Number, threshold: Number, *, atol: float = RATIO_EPS) -> bool:
    """Inclusive ratio comparison used by the 10% local-anomaly boundary."""
    return leq(ratio, threshold, atol=atol)


def threshold_leq(value: Number, threshold: Number, *, atol: float = THRESHOLD_EPS) -> bool:
    """Inclusive comparison against an engineering threshold (e.g. ±1.0 mm)."""
    return leq(value, threshold, atol=atol)


def threshold_geq(value: Number, threshold: Number, *, atol: float = THRESHOLD_EPS) -> bool:
    return geq(value, threshold, atol=atol)
