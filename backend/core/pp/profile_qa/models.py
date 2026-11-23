"""Serializable QA data models. Extra fields only — never rename existing PP keys."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Optional

import numpy as np

from .config import ALGORITHM_VERSION, QA_QUANTITY, QA_STORAGE_QUANTITY
from .state import QA_PENDING


def _to_native(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return [_to_native(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        val = value.item()
        if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
            return None
        return val
    if isinstance(value, float) and (np.isnan(value) or np.isinf(value)):
        return None
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _to_native(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _to_native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_native(v) for v in value]
    return value


@dataclass
class ProfileArrays:
    """Working copy of a formline. s is cumulative arc length (mm)."""
    s: np.ndarray
    circumference: np.ndarray
    diameter: np.ndarray
    source: str = "stp_raw"

    def copy(self) -> "ProfileArrays":
        return ProfileArrays(
            s=np.array(self.s, dtype=float, copy=True),
            circumference=np.array(self.circumference, dtype=float, copy=True),
            diameter=np.array(self.diameter, dtype=float, copy=True),
            source=self.source,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "s_mm": _to_native(self.s),
            "outer_circumference_mm": _to_native(self.circumference),
            "outer_diameter_mm": _to_native(self.diameter),
            "source": self.source,
            "point_count": int(len(self.s)),
        }

    def to_wall_thickness_overlay(self, original_rows: Optional[List[dict]] = None) -> List[dict]:
        """Build PP-compatible records without renaming existing keys."""
        rows: List[dict] = []
        originals = original_rows or []
        by_s = {}
        for row in originals:
            try:
                key = round(float(row.get("中心线长度(mm)", 0) or 0), 4)
            except (TypeError, ValueError):
                continue
            by_s[key] = dict(row)
        for i in range(len(self.s)):
            s_i = float(self.s[i])
            rec = dict(by_s.get(round(s_i, 4), {}))
            rec["中心线长度(mm)"] = round(s_i, 4)
            rec["外径周长(mm)"] = round(float(self.circumference[i]), 4)
            rows.append(rec)
        return rows


@dataclass
class SuspectSegment:
    start_s: float
    end_s: float
    start_index: int
    end_index: int
    reason: str = "local_monotonicity_violation"
    severity: str = "medium"
    max_positive_deviation: float = 0.0
    mean_residual: float = 0.0
    point_count: int = 0
    length: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return _to_native(asdict(self))


@dataclass
class StraightSegment:
    segment_id: str
    start_s: float
    end_s: float
    length: float
    representative_diameter: float
    max_deviation: float
    slope: float
    status: str = "candidate"
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    algorithm_version: str = ALGORITHM_VERSION
    start_index: int = 0
    end_index: int = 0
    representative_circumference: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return _to_native(asdict(self))


@dataclass
class FieldError:
    row: Optional[int]
    column: str
    reason: str
    how_to_fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _to_native(asdict(self))


def empty_qa_bundle() -> Dict[str, Any]:
    return {
        "status": QA_PENDING,
        "algorithm_version": ALGORITHM_VERSION,
        "qa_quantity": QA_QUANTITY,
        "storage_quantity": QA_STORAGE_QUANTITY,
        "raw_profile": None,
        "corrected_profile": None,
        "smoothed_profile": None,
        "working_profile": None,
        "processed_profile": None,
        "final_profile": None,
        "profile_source": "stp_raw",
        "suspect_segments": [],
        "straight_segments": [],
        "anomaly_mask": [],
        "anomaly_indices": [],
        "anomaly_point_ratio": 0.0,
        "anomaly_length_ratio": 0.0,
        "reason": "",
        "metrics": {},
        "warnings": [],
        "validation": {},
        "manual_override": None,
        "manual_profile": None,
        "auto_profile": None,
        "profile_choice": None,
        "L4": 0.0,
        "formline_length": None,
        "audit": [],
        "params": {},
        "qa_time": None,
        "smooth_before": None,
        "smooth_after": None,
        "input_fixes": [],
        "length_validation": None,
        "wall_thickness": None,
        "raw_section_dims": None,
        "corrected_section_dims": None,
        "key_section_records": [],
        "key_section_search": None,
        "key_section_stale_message": "",
    }
