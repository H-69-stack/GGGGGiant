"""Standalone manual formline generator: constant plateaus + PCHIP tapers.

Does not import or call STP profile smoothing.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional, Sequence

import numpy as np

from .config import SAMPLE_STEP_MM, SOURCE_NAME
from .constrain import constrain_control_points
from .interpolate import resample_manual_profile
from .normalize import normalize_control_points
from .validate import ManualProfileError, copy_raw_xlxs, validate_raw_xlxs


def generate_manual_profile(
    xlxs: Sequence[Any],
    *,
    y_is_circumference: bool = False,
    step_mm: Optional[float] = None,
) -> Dict[str, Any]:
    """Build a dense monotone manual formline from sparse control points.

    ``xlxs`` is the existing engineer list ``[[x, y], ...]``.
    ``y`` is outer diameter (mm) unless ``y_is_circumference`` is true.
    The original list is copied; the caller object is never mutated.
    """
    raw_xlxs = copy_raw_xlxs(xlxs)
    try:
        x_raw, y_raw = validate_raw_xlxs(raw_xlxs, check_diameter=not y_is_circumference)
        if y_is_circumference:
            y_raw = np.asarray(y_raw, dtype=float) / np.pi
            x_raw, y_raw = validate_raw_xlxs(
                [[float(a), float(b)] for a, b in zip(x_raw, y_raw)],
                check_diameter=True,
            )
        normalized = normalize_control_points(x_raw, y_raw)
        constrained = constrain_control_points(normalized["x"], normalized["y"])
        resampled = resample_manual_profile(
            constrained["x"],
            constrained["y"],
            constrained["segments"],
            step_mm=step_mm if step_mm is not None else SAMPLE_STEP_MM,
        )
    except ManualProfileError as exc:
        return {
            "ok": False,
            "code": exc.code,
            "message": exc.message,
            "raw_xlxs": raw_xlxs,
            "normalized_control_points": None,
            "constrained_control_points": None,
            "manual_profile": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": "MANUAL_PROFILE_INVALID",
            "message": str(exc) or "人工形线生成失败。",
            "raw_xlxs": raw_xlxs,
            "normalized_control_points": None,
            "constrained_control_points": None,
            "manual_profile": None,
        }

    s = np.asarray(resampled["s"], dtype=float)
    d = np.asarray(resampled["diameter"], dtype=float)
    c = d * np.pi
    diffs = np.diff(d)
    monotone = bool(np.all(diffs <= 1e-9))
    manual_profile = {
        "s_mm": [float(v) for v in s],
        "outer_circumference_mm": [float(v) for v in c],
        "outer_diameter_mm": [float(v) for v in d],
        "source": SOURCE_NAME,
        "point_count": int(len(s)),
    }
    return {
        "ok": True,
        "code": "",
        "message": "",
        "raw_xlxs": deepcopy(raw_xlxs),
        "normalized_control_points": normalized["points"],
        "constrained_control_points": constrained["points"],
        "segments": constrained["segments"],
        "reversed": bool(normalized.get("reversed")),
        "merged_groups": int(normalized.get("merged_groups") or 0),
        "n_clipped": int(constrained.get("n_clipped") or 0),
        "monotone": monotone,
        "manual_profile": manual_profile,
    }
