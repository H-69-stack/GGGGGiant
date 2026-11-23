"""Centralized engineering thresholds for STP profile QA.

All monotonic / straight / XLSX checks MUST import thresholds from here.
QA judgments operate on outer diameter (mm) derived from stored circumference:

    D = C / π
    C = π D

The existing PP pipeline continues to persist ``外径周长(mm)``; this module
does not change that on-disk / API field meaning.
"""
from __future__ import annotations

ALGORITHM_VERSION = "1.7.3"

# Quantity used by QA decision logic (derived; storage remains circumference).
QA_QUANTITY = "outer_diameter_mm"
QA_STORAGE_QUANTITY = "outer_circumference_mm"

# ---------------------------------------------------------------------------
# Numeric comparison
# ---------------------------------------------------------------------------
EPS = 1e-9
# Tight band so threshold+1e-9 is NOT treated as equal (see numerical boundary tests).
RATIO_EPS = 1e-12
THRESHOLD_EPS = 1e-12
LENGTH_EPS = 1e-9
# Length-compare band for L_stp vs L_drawing (mm). Covers float noise such as
# 1000.0000001 vs 999.9999999 without treating a real millimetre gap as equal.
LENGTH_COMPARE_TOLERANCE_MM = 1e-6

# ---------------------------------------------------------------------------
# Layer 1 — input legality
# ---------------------------------------------------------------------------
MIN_PROFILE_POINTS = 5
MAX_SAMPLE_GAP_MM = 25.0
MIN_CENTERLINE_LENGTH_MM = 1.0
MIN_DIAMETER_MM = 1.0
MAX_DIAMETER_MM = 2000.0
MAX_MISSING_RATIO = 0.20
DUPLICATE_S_MERGE_EPS_MM = 1e-6

# ---------------------------------------------------------------------------
# Layer 2 — monotonic / anomaly detection (all in outer-diameter mm)
# ---------------------------------------------------------------------------
ROLLING_MEDIAN_WINDOW = 5
MONOTONIC_NOISE_TOLERANCE_MM = 0.25
ANOMALY_RESIDUAL_K = 6.0
ANOMALY_RESIDUAL_ABS_MIN_MM = 0.80
MAX_LOCAL_ANOMALY_POINT_RATIO = 0.10
MAX_LOCAL_ANOMALY_LENGTH_RATIO = 0.12
MAX_LOCAL_ANOMALY_SEGMENTS = 4
MIN_ANOMALY_SEGMENT_LENGTH_MM = 2.0
ANOMALY_MERGE_GAP_POINTS = 2
# Expand a residual-peak run to the full geometric bump (return to local baseline).
BUMP_RETURN_EPS_MM = 0.05
BUMP_PEAK_PROMINENCE_MM = 0.40
BUMP_BASELINE_WINDOW_POINTS = 25
BUMP_MIN_LENGTH_MM = 8.0
# Detection / identification uses OD rounded to this many decimal places.
# C/π quantization (~0.003 mm) is below 0.01 mm and must not create bulges.
DIAMETER_DECIMALS = 2
# Sustained OD increase along s (independent of isotonic residual).
# Isotonic PAVA pools a mid-taper hill into the envelope, so a 0.18 mm
# rise can leave < 0.10 mm residual and miss ANOMALY_RESIDUAL_ABS_MIN_MM.
LOCAL_RISE_MIN_MM = 0.12
LOCAL_RISE_MIN_LENGTH_MM = 6.0
# After the sudden-rise start, more than this many subsequent rising points
# are required before the run is a bulge (「上升趋势超过 3 个点」).
LOCAL_RISE_MIN_POINTS = 3
# Minimum per-sample increase after 2-decimal rounding (one 0.01 mm step counts).
LOCAL_RISE_STEP_MIN_MM = 0.005
TREND_FIT_QUALITY_MIN = 0.12
TREND_SCATTER_RATIO_MAX = 0.30
TREND_MAD_MAX_MM = 2.50
# Sign flips at or below one OD quantum (0.01 mm) are rounding sawtooth, not oscillation.
OSCILLATION_SIGN_MIN_MM = 10 ** (-DIAMETER_DECIMALS)
SEVERITY_MEDIUM_MM = 1.0
SEVERITY_HIGH_MM = 3.0

# ---------------------------------------------------------------------------
# Local smoothing
# ---------------------------------------------------------------------------
SMOOTHING_METHOD = "local_monotone_trend"
SMOOTHING_ENDPOINT_BLEND_POINTS = 1
# Linear dD/ds from this much formline *outside* the suspect segment.
SMOOTHING_TREND_WINDOW_MM = 20.0
# Extra write-back so a monotone cubic can keep those outside slopes.
SMOOTHING_MAX_EXPAND_MM = 40.0

# ---------------------------------------------------------------------------
# Layer 3 — straight segments (outer-diameter mm)
# ---------------------------------------------------------------------------
STRAIGHT_TOLERANCE_MM = 1.0  # ±1.0 mm around representative OD
STRAIGHT_MAX_SLOPE = 0.0015  # |dD/ds| mm/mm
MIN_STRAIGHT_LENGTH_MM = 50.0
STRAIGHT_WINDOW_MM = 40.0
# Inward-only window used to trim a grown candidate back to the plateau
# edge (avoids eating the last 1 mm of a large-end taper).
STRAIGHT_GROW_WINDOW_MM = 20.0
MIN_STRAIGHT_POINTS = 8

# ---------------------------------------------------------------------------
# Drawing-length validation / formline correction (before smoothing)
# ---------------------------------------------------------------------------
# Median window at each end used to decide which end is the small-diameter side.
SMALL_END_WINDOW_MM = 5.0
# If |D_start - D_end| is within this band, do not silently pick a trim/extend side.
SMALL_END_SIZE_DELTA_MM = 0.50
# Local window for deciding straight vs taper when extending a short STP formline.
LENGTH_EXTEND_TREND_WINDOW_MM = 40.0

# ---------------------------------------------------------------------------
# Key-section search (outer section length × width, mm)
# ---------------------------------------------------------------------------
SECTION_MATCH_TOLERANCE_MM = 1.0  # ±1.0 mm
# Consecutive 1 mm hits with a gap this small belong to the same range.
SECTION_RANGE_GAP_MM = 2.0

# ---------------------------------------------------------------------------
# Wall-thickness confirmation (T1..T4 ↔ TL1..TL4)
# ---------------------------------------------------------------------------
WALL_LENGTH_KEYS = ("TL1", "TLM1", "TL2", "TLM2", "TL3", "TLM3", "TL4")
WALL_THICKNESS_KEYS = ("T1", "T2", "T3", "T4")
WALL_THICKNESS_LENGTH_PAIRS = (("T1", "TL1"), ("T2", "TL2"), ("T3", "TL3"), ("T4", "TL4"))

# ---------------------------------------------------------------------------
# XLSX manual profile
# ---------------------------------------------------------------------------
MAX_XLSX_BYTES = 5 * 1024 * 1024
XLSX_MIN_POINTS = 2
XLSX_CENTERLINE_COL = "centerline_mm"
XLSX_CIRCUMFERENCE_COL = "outer_circumference_mm"
XLSX_DIAMETER_COL = "outer_diameter_mm"
# Legacy Chinese headers already used by PP manual upload (accepted as aliases).
XLSX_CENTERLINE_ALIASES = ("centerline_mm", "中心线长度(mm)", "中心线位置(mm)")
XLSX_CIRCUMFERENCE_ALIASES = ("outer_circumference_mm", "外径周长(mm)", "截面周长(mm)")
XLSX_DIAMETER_ALIASES = ("outer_diameter_mm", "外径(mm)")
